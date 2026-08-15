import { useState } from 'react';
import { api } from '../services/api';

export default function MeetingChat({ meetingId }) {
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState([]); // { role: 'user' | 'assistant', text }
  const [sessionId, setSessionId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleAsk(e) {
    e.preventDefault();
    const q = question.trim();
    if (!q || loading) return;

    setMessages((m) => [...m, { role: 'user', text: q }]);
    setQuestion('');
    setLoading(true);
    setError(null);

    try {
      const res = await api.askMeeting(meetingId, q, sessionId);
      setSessionId(res.session_id);
      setMessages((m) => [...m, { role: 'assistant', text: res.answer }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ marginTop: 14, borderTop: '1px solid #eee', paddingTop: 10 }}>
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Ask about this meeting</div>

      {messages.length > 0 && (
        <div style={{ maxHeight: 260, overflowY: 'auto', marginBottom: 8 }}>
          {messages.map((m, i) => (
            <div
              key={i}
              style={{
                marginBottom: 8,
                textAlign: m.role === 'user' ? 'right' : 'left',
              }}
            >
              <span
                style={{
                  display: 'inline-block',
                  maxWidth: '85%',
                  padding: '6px 10px',
                  borderRadius: 8,
                  fontSize: 13,
                  background: m.role === 'user' ? '#2563eb' : '#f1f5f9',
                  color: m.role === 'user' ? '#fff' : '#111',
                  textAlign: 'left',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {m.text}
              </span>
            </div>
          ))}
        </div>
      )}

      {loading && <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>Thinking…</div>}
      {error && <div style={{ fontSize: 12, color: '#dc2626', marginBottom: 8 }}>{error}</div>}

      <form onSubmit={handleAsk} style={{ display: 'flex', gap: 6 }}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. What did we decide about the budget?"
          style={{
            flex: 1,
            fontSize: 13,
            padding: '6px 8px',
            border: '1px solid #ddd',
            borderRadius: 6,
          }}
        />
        <button
          type="submit"
          disabled={loading || !question.trim()}
          style={{
            fontSize: 13,
            fontWeight: 600,
            padding: '6px 12px',
            borderRadius: 6,
            border: 'none',
            background: '#2563eb',
            color: '#fff',
            cursor: loading ? 'default' : 'pointer',
            opacity: loading || !question.trim() ? 0.6 : 1,
          }}
        >
          Ask
        </button>
      </form>
    </div>
  );
}