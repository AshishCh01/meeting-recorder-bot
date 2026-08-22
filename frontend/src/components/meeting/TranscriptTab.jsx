import React from 'react';

export const TranscriptTab = ({ conversation }) => {
  if (!conversation || !Array.isArray(conversation) || conversation.length === 0) {
    return <p className="text-muted italic text-sm">No transcript available.</p>;
  }

  return (
    <div className="flex flex-col gap-4.5">
      {conversation.map((seg, i) => {
        if (!seg.text) return null;

        let speaker = 'Unknown';
        let text = seg.text;
        const match = seg.text.match(/^([^:-]+)[:-]\s*(.*)/);
        if (match) {
          speaker = match[1].trim();
          text = match[2].trim();
        }

        return (
          <div key={i} className="flex gap-4">
            <span className="flex-none w-13 font-mono text-xs text-faint pt-0.5">{seg.timestamp_start}</span>
            <div className="min-w-0">
              <div className="text-[13.5px] font-extrabold text-brand-dark">{speaker}</div>
              <div className="text-[15px] leading-relaxed text-body mt-0.5">{text}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
};
