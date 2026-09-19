import { useEffect } from 'react';
import api from '../lib/api';
import { nextMessageId, useStreamingChat } from './useStreamingChat';

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
// Sending and streaming are useStreamingChat's, shared with Ask AI.
export function useMeetingChat(meetingId) {
  const chat = useStreamingChat({
    streamPath: `/meetings/${meetingId}/chat/stream`,
    buildBody: (text) => ({ question: text }),
    toolLabels: TOOL_LABELS,
    initialMessages: [INITIAL_MESSAGE],
  });
  const { setMessages } = chat;

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
  }, [meetingId, setMessages]);

  const { messages, input, setInput, loading, streaming, status, sendMessage, messagesEndRef } = chat;
  return { messages, input, setInput, loading, streaming, status, sendMessage, messagesEndRef };
}
