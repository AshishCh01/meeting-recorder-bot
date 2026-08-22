import React from 'react';

export const SummaryTab = ({ summary, keyPoints, conclusion }) => {
  if (!summary && (!keyPoints || keyPoints.length === 0) && !conclusion) {
    return <p className="text-muted italic text-sm">No summary generated yet.</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      {summary && (
        <div className="p-5 rounded-xl bg-sidebar border border-line">
          <div className="text-xs font-extrabold tracking-widest text-brand-blue mb-2">TL;DR</div>
          <div className="text-[15px] leading-relaxed text-brand-dark">{summary}</div>
        </div>
      )}

      {keyPoints && keyPoints.length > 0 && (
        <div className="flex flex-col gap-3">
          <div className="text-xs font-extrabold tracking-widest text-muted">KEY POINTS</div>
          {keyPoints.map((point, i) => (
            <div key={i} className="flex gap-3">
              <span className="flex-none w-1.5 h-1.5 rounded-full bg-brand-blue-light mt-2" />
              <div className="text-sm leading-relaxed text-body">{point}</div>
            </div>
          ))}
        </div>
      )}

      {conclusion && (
        <div className="flex flex-col gap-2">
          <div className="text-xs font-extrabold tracking-widest text-muted">CONCLUSION</div>
          <div className="text-sm leading-relaxed text-body">{conclusion}</div>
        </div>
      )}
    </div>
  );
};
