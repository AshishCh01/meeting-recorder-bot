import { useEffect, useRef, useState } from 'react';
import { readChatStream } from '../lib/chatStream';

// Every message carries a stable id, assigned once and never reused, so the
// streaming reply can be grown by id instead of by position - see pushDelta.
let messageSeq = 0;
export const nextMessageId = () => `msg-${++messageSeq}`;

// The send/stream state machine both chats share: the meeting chat
// (useMeetingChat) and Ask AI (useAskAiChat). Each supplies where to stream,
// what to send, how to label its tools, and what to do with any event beyond
// the common ones (Ask AI's `title`); loading stored history stays with them.
//
//   streamPath     backend path of the SSE route
//   buildBody      (text) => the JSON body for one question
//   toolLabels     tool name -> "Searching…" label shown while it runs
//   onEvent        (event) => called for events other than delta/reset/tool
//   initialMessages the messages to start from
export function useStreamingChat({ streamPath, buildBody, toolLabels, onEvent, initialMessages = [] }) {
  const [messages, setMessages] = useState(initialMessages);
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
    // `block: 'nearest'` scrolls the message list and nothing else. The
    // default is 'start', which scrolls every ancestor scroll container -
    // including the window - so on the meeting page, where the page is now
    // the only scroll container, the greeting dragged the whole page down by
    // 56px on load and pulled the sticky header off the top of the screen.
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading, status]);

  // Abandon an in-flight answer if the user navigates away mid-stream.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // For a caller switching conversations under a mounted chat.
  const abort = () => abortRef.current?.abort();

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
      await readChatStream(streamPath, buildBody(text), {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === 'delta') {
            pushDelta(event.text);
          } else if (event.type === 'reset') {
            resetReply();
          } else if (event.type === 'tool') {
            if (!replyText) setStatus(toolLabels[event.name] || 'Working…');
          } else {
            // `done` needs no handling here: the answer is already fully rendered.
            onEvent?.(event);
          }
        },
      });

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

  return { messages, setMessages, input, setInput, loading, streaming, status, sendMessage, messagesEndRef, abort };
}
