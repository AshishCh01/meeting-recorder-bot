import React from 'react';

/**
 * The text gets the room.
 *
 * These were flex rows where an owner pill and a fixed 80px due-date column
 * took their width first, leaving the item itself squeezed to about a word per
 * line on a phone. Now it is a grid: the text column is `minmax(0, 1fr)` and
 * the owner and date share one `auto` column that wraps under the text when
 * there is no room for it beside.
 */
export const ActionItemsList = ({ actionItems }) => {
  if (!actionItems || actionItems.length === 0) {
    return <p className="text-sm italic text-muted">No action items found.</p>;
  }

  return (
    <div className="flex flex-col">
      {actionItems.map((item, i) => (
        <article
          key={i}
          className="grid grid-cols-[18px_minmax(0,1fr)] items-start gap-x-3 gap-y-2 border-b border-line py-3.5 tablet:grid-cols-[18px_minmax(0,1fr)_auto]"
        >
          {/* Decorative only - the transcript JSON has no "done" field to
              wire this to, so it is not a checkbox and does not claim to be. */}
          <span aria-hidden="true" className="mt-0.5 h-[18px] w-[18px] flex-none rounded-[5px] border-[1.5px] border-border" />

          <div className="min-w-0">
            <p className="break-words text-[14.5px] font-semibold leading-snug text-brand-dark">{item.item}</p>
            {item.timestamp && <p className="mt-0.5 text-xs text-muted">from {item.timestamp}</p>}
          </div>

          <div className="col-start-2 flex items-center gap-2.5 tablet:col-start-3 tablet:row-start-1">
            <span className="inline-flex flex-none items-center gap-1.5 rounded-full bg-tint-2 py-1 pl-1 pr-2.5 text-xs font-bold text-body">
              <span className="grid h-4.5 w-4.5 place-items-center rounded-full bg-tint-3 text-[9.5px] uppercase">
                {(item.owner || '?').charAt(0)}
              </span>
              {item.owner || 'Unassigned'}
            </span>
            {item.due_date && <span className="flex-none text-[12.5px] text-muted">{item.due_date}</span>}
          </div>
        </article>
      ))}
    </div>
  );
};
