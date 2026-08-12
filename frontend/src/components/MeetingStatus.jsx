const COLORS = {
  scheduled: '#888',
  joining: '#d97706',
  recording: '#dc2626',
  uploading: '#2563eb',
  completed: '#16a34a',
  failed: '#991b1b',
};

export default function MeetingStatus({ status }) {
  const color = COLORS[status] || '#888';
  return (
    <span
      style={{
        fontSize: 12,
        fontWeight: 600,
        color: '#fff',
        background: color,
        borderRadius: 12,
        padding: '2px 10px',
        textTransform: 'capitalize',
      }}
    >
      {status}
    </span>
  );
}
