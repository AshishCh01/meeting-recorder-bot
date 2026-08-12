import { useEffect, useState, useCallback } from 'react';
import { api } from '../services/api.js';
import MeetingForm from '../components/MeetingForm.jsx';
import RecordingCard from '../components/RecordingCard.jsx';

export default function Dashboard() {
  const [meetings, setMeetings] = useState([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    api.listMeetings()
      .then(setMeetings)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
    // Poll every 5s so in-progress meetings (joining/recording/uploading)
    // update to completed without the user needing to refresh manually.
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  function handleCreated(newMeeting) {
    setMeetings((prev) => [newMeeting, ...prev]);
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: '2rem 1rem' }}>
      <h1>Meeting Recorder</h1>
      <MeetingForm onCreated={handleCreated} />

      {loading && <p>Loading...</p>}
      {!loading && meetings.length === 0 && <p>No meetings yet — paste a link above to get started.</p>}

      {meetings.map((m) => (
        <RecordingCard key={m.id} meeting={m} />
      ))}
    </div>
  );
}
