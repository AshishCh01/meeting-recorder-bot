import { useEffect, useRef, useState } from 'react';
import { supabase } from '../lib/supabase';

const INITIAL_MESSAGE = {
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

  const sendMessage = async (overrideText) => {
    const text = (overrideText ?? input).trim();
    if (!text || loading) return;

    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: text }]);
    setLoading(true);
    setStreaming(false);
    setStatus('');

    const controller = new AbortController();
    abortRef.current = controller;

    // Appends the first delta as a new agent bubble, then grows that same
    // bubble with each subsequent delta.
    let started = false;
    const pushDelta = (chunk) => {
      setMessages(prev => {
        if (!started) return [...prev, { role: 'agent', content: chunk }];
        const next = [...prev];
        next[next.length - 1] = {
          ...next[next.length - 1],
          content: next[next.length - 1].content + chunk,
        };
        return next;
      });
      started = true;
      setStreaming(true);
      setStatus('');
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
          } else if (event.type === 'tool') {
            if (!started) setStatus(TOOL_LABELS[event.name] || 'Working…');
          } else if (event.type === 'error') {
            throw new Error(event.message);
          }
          // `done` needs no handling: the answer is already fully rendered.
        }
      }

      if (!started) {
        setMessages(prev => [...prev, {
          role: 'agent',
          content: '*No answer was returned. Please try again.*',
        }]);
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      console.error('Chat error:', err);
      // If part of the answer already streamed in, it stays on screen and the
      // failure is appended after it rather than replacing it.
      setMessages(prev => [...prev, {
        role: 'agent',
        content: `*${err.message || 'Sorry, I encountered an error communicating with the server.'}*`,
      }]);
    } finally {
      abortRef.current = null;
      setStatus('');
      setStreaming(false);
      setLoading(false);
    }
  };

  return { messages, input, setInput, loading, streaming, status, sendMessage, messagesEndRef };
}
