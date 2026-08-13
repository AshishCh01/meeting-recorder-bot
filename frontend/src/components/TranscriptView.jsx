import { useState } from 'react';

export default function TranscriptView({ transcript }) {
  const [expanded, setExpanded] = useState(false);

  if (!transcript) return null;

  const { summary, key_points, action_items, conclusion, conversation } = transcript;

  return (
    <div style={{ marginTop: 10, borderTop: '1px solid #eee', paddingTop: 10 }}>
      <button
        onClick={() => setExpanded((e) => !e)}
        style={{
          background: 'none',
          border: 'none',
          color: '#2563eb',
          fontSize: 13,
          fontWeight: 600,
          cursor: 'pointer',
          padding: 0,
        }}
      >
        {expanded ? 'Hide transcript ▲' : 'View transcript ▼'}
      </button>

      {expanded && (
        <div style={{ marginTop: 10, fontSize: 13, color: '#333' }}>
          {summary && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontWeight: 600, marginBottom: 2 }}>Summary</div>
              <div>{summary}</div>
            </div>
          )}

          {key_points?.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontWeight: 600, marginBottom: 2 }}>Key points</div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {key_points.map((point, i) => (
                  <li key={i}>{point}</li>
                ))}
              </ul>
            </div>
          )}

          {action_items?.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontWeight: 600, marginBottom: 2 }}>Action items</div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {action_items.map((item, i) => (
                  <li key={i}>
                    {item.item}
                    {item.owner && item.owner !== 'Unspecified' && (
                      <span style={{ color: '#666' }}> — {item.owner}</span>
                    )}
                    {item.timestamp && (
                      <span style={{ color: '#999' }}> ({item.timestamp})</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {conclusion && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontWeight: 600, marginBottom: 2 }}>Conclusion</div>
              <div>{conclusion}</div>
            </div>
          )}

          {conversation?.length > 0 && (
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Transcript</div>
              <div style={{ maxHeight: 240, overflowY: 'auto', border: '1px solid #eee', borderRadius: 6, padding: '8px 10px' }}>
                {conversation.map((seg, i) => (
                  <div key={i} style={{ marginBottom: 6 }}>
                    <span style={{ color: '#999', fontSize: 11, marginRight: 6 }}>
                      {seg.timestamp_start}–{seg.timestamp_end}
                    </span>
                    <span>{seg.text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}