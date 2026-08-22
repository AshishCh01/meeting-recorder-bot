import React from 'react';

export const ActionItemsList = ({ actionItems }) => {
  if (!actionItems || actionItems.length === 0) {
    return <p className="text-muted italic text-sm">No action items found.</p>;
  }

  return (
    <div className="flex flex-col gap-2.5">
      {actionItems.map((item, i) => (
        <div key={i} className="flex items-center gap-3.5 px-4 py-3.5 border border-line rounded-xl">
          {/* Decorative only - the transcript JSON has no "done" field to wire this to. */}
          <span className="flex-none w-[18px] h-[18px] rounded-[5px] border-[1.5px] border-border-strong" />
          <div className="flex-1 min-w-0">
            <div className="text-[15px] font-semibold text-brand-dark">{item.item}</div>
            {item.timestamp && <div className="text-xs text-muted mt-0.5">from {item.timestamp}</div>}
          </div>
          <span className="flex-none px-2.5 py-1 rounded-full bg-status-muted-bg text-xs font-bold text-status-muted-fg">
            {item.owner || 'Unspecified'}
          </span>
          {item.due_date && (
            <span className="flex-none text-xs text-muted w-20 text-right">{item.due_date}</span>
          )}
        </div>
      ))}
    </div>
  );
};
