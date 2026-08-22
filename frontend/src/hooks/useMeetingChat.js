import { useEffect, useRef, useState } from 'react';
import api from '../lib/api';

const INITIAL_MESSAGE = {
  role: 'agent',
  content: 'Hi! I am your AI assistant for this meeting. You can ask me to search the transcript, summarize points, or find action items.',
};

// Shared chat state/logic for a meeting's RAG assistant - used by both the
// desktop permanent chat column and the mobile drag-up sheet, so the two
// surfaces never own two separate conversations for the same meeting.
export function useMeetingChat(meetingId) {
  const [messages, setMessages] = useState([INITIAL_MESSAGE]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  const sendMessage = async (overrideText) => {
    const text = (overrideText ?? input).trim();
    if (!text || loading) return;

    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: text }]);
    setLoading(true);

    try {
      const { data } = await api.post(`/meetings/${meetingId}/chat`, { question: text });
      setMessages(prev => [...prev, { role: 'agent', content: data.answer }]);
    } catch (err) {
      console.error('Chat error:', err);
      let errorMsg = '*Sorry, I encountered an error communicating with the server.*';
      if (err.code === 'ECONNABORTED') {
        errorMsg = '*That took too long. The AI service is currently experiencing high load. Please try again.*';
      }
      setMessages(prev => [...prev, { role: 'agent', content: errorMsg }]);
    } finally {
      setLoading(false);
    }
  };

  return { messages, input, setInput, loading, sendMessage, messagesEndRef };
}
