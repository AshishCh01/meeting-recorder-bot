import { useEffect, useRef, useState } from 'react';
import api from '../lib/api';
import { useStreamingChat } from './useStreamingChat';

// What the assistant is doing while each Ask AI tool runs.
const TOOL_LABELS = {
  list_meetings: 'Finding meetings…',
  find_meetings_by_topic: 'Finding relevant meetings…',
  get_meeting_details: 'Reading meeting summaries…',
  get_meeting_action_items: 'Collecting action items…',
  search_across_meetings: 'Searching transcripts…',
};

// Dates in questions ("yesterday", "15/09") are resolved in the user's own
// timezone, so the browser's goes with every question.
const browserTimezone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
};

// One Ask AI chat: its stored messages, streaming new questions into it, and
// its title. `state` is "loading", "ready", "missing" (deleted, or not this
// user's - the API answers both with a 404) or "error".
//
//   onTitle(conversationId, title)   the chat was named after its first question
//   onAnswered(conversationId)       an answer finished streaming
export function useAskAiChat(conversationId, { onTitle, onAnswered } = {}) {
  const [state, setState] = useState('loading');
  const [title, setTitle] = useState('New chat');
  // The page's callbacks change every render; the stream's handlers read the
  // latest through this rather than whichever render started the question.
  const callbacks = useRef({ onTitle, onAnswered });
  useEffect(() => {
    callbacks.current = { onTitle, onAnswered };
  });

  const chat = useStreamingChat({
    streamPath: `/ask/conversations/${conversationId}/stream`,
    buildBody: (text) => ({ question: text, timezone: browserTimezone() }),
    toolLabels: TOOL_LABELS,
    onEvent: (event) => {
      if (event.type === 'title') {
        setTitle(event.title);
        callbacks.current.onTitle?.(conversationId, event.title);
      } else if (event.type === 'done') {
        callbacks.current.onAnswered?.(conversationId);
      }
    },
  });
  const { setMessages, abort } = chat;

  // Opening a chat - or switching to another under the same page - drops the
  // previous one's answer in flight and loads this one's stored messages.
  useEffect(() => {
    if (!conversationId) return undefined;
    let cancelled = false;
    abort();
    setMessages([]);
    setTitle('New chat');
    setState('loading');

    api.get(`/ask/conversations/${conversationId}/messages`)
      .then(({ data }) => {
        if (cancelled) return;
        setTitle(data.title);
        setMessages((data.messages ?? []).map((m) => ({
          // Prefixed so a row id can never collide with the `msg-N` ids
          // minted for messages sent in this session.
          id: `db-${m.id}`,
          role: m.role === 'assistant' ? 'agent' : 'user',
          content: m.content,
        })));
        setState('ready');
      })
      .catch((err) => {
        if (cancelled) return;
        setState(err.response?.status === 404 ? 'missing' : 'error');
      });

    return () => { cancelled = true; };
    // abort and setMessages are stable in effect; only a new chat reloads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  return { ...chat, state, title };
}
