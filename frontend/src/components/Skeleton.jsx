import React from 'react';

// A bare centred spinner says "something is happening" and nothing else. A
// skeleton says what is about to arrive and how much of it, so the page does
// not jump when it does. `animate-pulse` is already disabled by the reduced
// motion rule in index.css, which leaves a static grey shape - still useful.
const Bar = ({ className = '' }) => <span className={`block rounded-md bg-tint-2 ${className}`} />;

/** Rows for the meetings list and Upcoming, matching their real row heights. */
export const RowSkeleton = ({ rows = 5 }) => (
  <div aria-hidden="true" className="animate-pulse">
    {Array.from({ length: rows }, (_, i) => (
      <div key={i} className="flex items-center gap-3 border-b border-line px-2.5 py-3">
        <Bar className="h-8.5 w-8.5 flex-none rounded-[10px]" />
        <div className="min-w-0 flex-1">
          <Bar className="h-3.5 w-2/5" />
          <Bar className="mt-2 h-3 w-1/2" />
        </div>
        <Bar className="hidden h-3 w-32 flex-none tablet:block" />
      </div>
    ))}
  </div>
);

/** The meeting page: header line, then a summary-shaped block. */
export const MeetingSkeleton = () => (
  <div aria-hidden="true" className="animate-pulse pt-5">
    <Bar className="h-6 w-2/3 max-w-sm" />
    <Bar className="mt-3 h-3.5 w-48" />
    <Bar className="mt-8 h-24 rounded-xl" />
    <Bar className="mt-6 h-3.5 w-28" />
    <Bar className="mt-4 h-3.5" />
    <Bar className="mt-2.5 h-3.5 w-11/12" />
    <Bar className="mt-2.5 h-3.5 w-4/5" />
  </div>
);

/** The Ask AI thread list. */
export const ThreadSkeleton = ({ rows = 6 }) => (
  <div aria-hidden="true" className="animate-pulse flex flex-col gap-2 px-3">
    {Array.from({ length: rows }, (_, i) => (
      <Bar key={i} className="h-8" />
    ))}
  </div>
);

/**
 * A screen-reader announcement to go with any of the above. The skeletons are
 * aria-hidden decoration; this is the part that is actually read out.
 */
export const LoadingLabel = ({ children = 'Loading…' }) => (
  <span role="status" aria-live="polite" className="sr-only">
    {children}
  </span>
);
