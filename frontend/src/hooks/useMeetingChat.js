import { useEffect, useRef, useState } from 'react';
import api from '../lib/api';
import { supabase } from '../lib/supabase';

// Every message carries a stable id, assigned once and never reused, so the
// streaming reply can be grown by id instead of by position - see pushDelta.
let messageSeq = 0;
const nextMessageId = () => `msg-${++messageSeq}`;

const INITIAL_MESSAGE = {
  id: nextMessageId(),
  role: 'agent',
  content: 'Hi! I am your AI assistant for this meeting. You can ask me to search the transcript, summarize points, or find action items.',
};

// Human-readable labels for the tool names the backend streams as `tool`
// events, so the wait shows what the assistant is actually doing.
const TOOL_LABELS = {
  get_meeting_summary: 'Reading the summary…',
  get_action_items: 'Collecting action items…',
  search_by_speaker: 'Searching by speaker…',
  search_transcript: 'Searching the transcript…',
};

// Shared chat state/logic for a meeting's RAG assistant - used by both the
// desktop permanent chat column and the mobile drag-up sheet, so the two
// surfaces never own two separate conversations for the same meeting.
export function useMeetingChat(meetingId) {
  const [messages, setMessages] = useState([INITIAL_MESSAGE]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  // True once the first piece of the answer has arrived, so the UI can drop
  // the typing indicator and let the answer itself show the progress.
  const [streaming, setStreaming] = useState(false);
  // What the agent is doing right now ("Searching the transcript…"), shown
  // until the first piece of the answer arrives.
  const [status, setStatus] = useState('');
  const messagesEndRef = useRef(null);
  const abortRef = useRef(null);
  // Mirrors `loading`, but updates synchronously - see the guard in sendMessage.
  const sendingRef = useRef(false);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading, status]);

  // Abandon an in-flight answer if the user navigates away mid-stream.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // Load the meeting's stored thread. The backend keeps one durable thread
  // per meeting, so this is what makes the conversation outlive a refresh,
  // a logout or a backend restart rather than restarting at the greeting.
  useEffect(() => {
    let cancelled = false;
    setMessages([INITIAL_MESSAGE]);

    api.get(`/meetings/${meetingId}/chat`)
      .then(({ data }) => {
        if (cancelled) return;
        const stored = (data?.messages ?? []).map((m) => ({
          // Prefixed so a row id can never collide with the `msg-N` ids
          // minted for messages sent in this session.
          id: `db-${m.id}`,
          role: m.role === 'assistant' ? 'agent' : 'user',
          content: m.content,
        }));
        if (!stored.length) return;
        // Only the greeting is replaced. Anything the user managed to send
        // while this request was in flight is kept, after the stored thread.
        setMessages(prev => [
          INITIAL_MESSAGE,
          ...stored,
          ...prev.filter(m => m.id !== INITIAL_MESSAGE.id),
        ]);
      })
      .catch(() => {
        // No stored thread, or the meeting isn't chattable yet (the history
        // endpoint is gated like posting a question). Either way the panel
        // just opens on the greeting - nothing to report to the user.
      });

    return () => { cancelled = true; };
  }, [meetingId]);

  const sendMessage = async (overrideText) => {
    const text = (overrideText ?? input).trim();
    // Guard on the ref rather than on `loading`: state settles a render later,
    // so a double-click, or Enter plus a suggestion chip in the same tick, both
    // read the stale `false` and start two streams that then interleave their
    // deltas into one message list. The ref flips now, so the second is dropped.
    if (!text || sendingRef.current) return;
    sendingRef.current = true;

    setInput('');
    setMessages(prev => [...prev, { id: nextMessageId(), role: 'user', content: text }]);
    setLoading(true);
    setStreaming(false);
    setStatus('');

    const controller = new AbortController();
    abortRef.current = controller;

    // The reply owns an id before a single token arrives, and every delta is
    // written to *that* message. The previous version grew "the last message in
    // the array" behind a plain `started` flag, so anything appended after the
    // first delta made the rest of the answer land in the wrong bubble - most
    // visibly the user's own, which then rendered the assistant's reply as
    // plain text inside the blue question bubble.
    const replyId = nextMessageId();
    let replyText = '';

    const writeReply = (content) => {
      replyText = content;
      setMessages(prev => {
        const idx = prev.findIndex(m => m.id === replyId);
        if (idx === -1) return [...prev, { id: replyId, role: 'agent', content }];
        const next = [...prev];
        next[idx] = { ...next[idx], content };
        return next;
      });
    };

    const pushDelta = (chunk) => {
      writeReply(replyText + chunk);
      setStreaming(true);
      setStatus('');
    };

    // The backend sends `reset` when it re-runs a turn whose first tokens
    // already reached us - a dropped Gemini stream, or a hand-off to the Groq
    // fallback. Dropping the bubble lets the second attempt write the answer
    // from scratch instead of continuing a sentence nobody finished.
    const resetReply = () => {
      replyText = '';
      setMessages(prev => prev.filter(m => m.id !== replyId));
      setStreaming(false);
    };

    try {
      const { data: { session } } = await supabase.auth.getSession();
      const response = await fetch(
        `${import.meta.env.VITE_API_URL}/meetings/${meetingId}/chat/stream`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
          },
          body: JSON.stringify({ question: text }),
          signal: controller.signal,
        }
      );

      if (!response.ok || !response.body) {
        // Ownership/readiness failures still arrive as normal status codes,
        // because the backend validates before the stream starts.
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail || `Request failed (${response.status})`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      // Minimal SSE reader: frames are separated by a blank line, and a frame
      // can straddle two network chunks, so hold the remainder in `buffer`.
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';

        for (const frame of frames) {
          const line = frame.split('\n').find(l => l.startsWith('data:'));
          if (!line) continue;

          let event;
          try {
            event = JSON.parse(line.slice(5).trim());
          } catch {
            continue;
          }

          if (event.type === 'delta') {
            pushDelta(event.text);
          } else if (event.type === 'reset') {
            resetReply();
          } else if (event.type === 'tool') {
            if (!replyText) setStatus(TOOL_LABELS[event.name] || 'Working…');
          } else if (event.type === 'error') {
            throw new Error(event.message);
          }
          // `done` needs no handling: the answer is already fully rendered.
        }
      }

      if (!replyText) {
        setMessages(prev => [...prev, {
          id: nextMessageId(),
          role: 'agent',
          content: '*No answer was returned. Please try again.*',
        }]);
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      console.error('Chat error:', err);
      const content = `*${err.message || 'Sorry, I encountered an error communicating with the server.'}*`;
      setMessages(prev => [
        // Any answer that streamed in stays on screen and the failure is
        // appended after it; an empty reply bubble (one the backend reset and
        // then never refilled) is dropped so the error isn't preceded by a
        // blank one.
        ...(replyText ? prev : prev.filter(m => m.id !== replyId)),
        { id: nextMessageId(), role: 'agent', content },
      ]);
    } finally {
      abortRef.current = null;
      sendingRef.current = false;
      setStatus('');
      setStreaming(false);
      setLoading(false);
    }
  };

  return { messages, input, setInput, loading, streaming, status, sendMessage, messagesEndRef };
}
