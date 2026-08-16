import { useEffect, useState } from 'react';
import { api } from '../services/api.js';
import RecordingCard from '../components/RecordingCard.jsx';

function Recordings() {
  const [meetings, setMeetings] = useState([]);
  const [loading, setLoading] = useState(true);
  
  useEffect(() => {
    async function loadMeetings() {
      try {
        const data = await api.listMeetings();
        setMeetings(Array.isArray(data) ? data : []);
      } catch (err) {
        console.error(err);
        setMeetings([]);
      } finally {
        setLoading(false);
      }
    }

    loadMeetings();
  }, []);
  console.log(meetings);

  if (loading) {
    return <div style={{ padding: 24 }}>Loading recordings...</div>;
  }

  return (
    <div className="p-10 mx-auto">
      {meetings.map((m) => (
        <RecordingCard key={m.id} meeting={m} />
      ))}
    </div>
  );
}

export { Recordings };