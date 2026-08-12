import { useState } from 'react';
import { api } from '../services/api.js';

export default function MeetingForm({ onCreated }) {
  const [url, setUrl] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!url.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const meeting = await api.createMeeting(url.trim());
      setUrl('');
      onCreated(meeting);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
      <input
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="Paste a Google Meet, Zoom, or Teams link..."
        style={{ flex: 1, padding: '10px 12px', border: '1px solid #ccc', borderRadius: 6 }}
      />
      <button type="submit" disabled={submitting} style={{ padding: '10px 16px' }}>
        {submitting ? 'Sending bot...' : 'Record meeting'}
      </button>
      {error && <p style={{ color: 'crimson', margin: 0 }}>{error}</p>}
    </form>
  );
}
