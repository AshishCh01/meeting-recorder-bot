import MeetingStatus from './MeetingStatus.jsx';
import TranscriptView from './TranscriptView.jsx';

export default function RecordingCard({ meeting }) {
  return (
    <div
      style={{
        border: '1px solid #ddd',
        borderRadius: 8,
        padding: '12px 16px',
        marginBottom: 10,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div>
          <div style={{ fontWeight: 600 }}>{meeting.platform} meeting</div>
          <div style={{ fontSize: 13, color: '#666', wordBreak: 'break-all' }}>{meeting.meeting_url}</div>
          {meeting.error_message && (
            <div style={{ fontSize: 12, color: 'crimson', marginTop: 4 }}>{meeting.error_message}</div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <MeetingStatus status={meeting.status} />
          {meeting.status === 'completed' && meeting.recording_url && (
            <a href={meeting.recording_url} target="_blank" rel="noreferrer">
              View recording
            </a>
          )}
        </div>
      </div>

      <TranscriptView transcript={meeting.transcript} />
    </div>
  );
}