import React from 'react';
import { Play } from 'lucide-react';

// A fixed decorative pattern, not real amplitude - the same shape the app's
// own player used before phase 6 replaced it with an honest progress bar.
const BARS = Array.from({ length: 48 }, (_, i) => 6 + Math.round(Math.abs(Math.sin(i * 0.9) * Math.cos(i * 0.31)) * 18));

/**
 * The hero's product shot: the real meeting page, drawn in JSX.
 *
 * It replaces a striped "product shot — meeting detail" placeholder, which was
 * the single worst thing on the page. Everything in it is sample content, so
 * it has to obey the same honesty rule as the copy: no figures, plans or
 * features that do not exist.
 *
 * The chat column drops away below 1000px, where three columns stop fitting.
 */
export const ProductMock = () => (
  <div className="relative mx-auto mt-14 w-full max-w-5xl px-5 sm:px-6">
    <div className="overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl shadow-brand-dark/10 dark:shadow-black/40">
      <div className="flex items-center gap-1.5 border-b border-line bg-sidebar px-3.5 py-2.5">
        <i className="h-2.5 w-2.5 rounded-full bg-tint-3" />
        <i className="h-2.5 w-2.5 rounded-full bg-tint-3" />
        <i className="h-2.5 w-2.5 rounded-full bg-tint-3" />
        <span className="ml-2.5 font-mono text-[10.5px] text-muted">app.meetiq.com/meetings</span>
      </div>

      <div className="grid grid-cols-[44px_minmax(0,1fr)] min-[1000px]:grid-cols-[56px_minmax(0,1fr)_240px]">
        <div className="flex flex-col items-center gap-3 border-r border-line bg-sidebar py-4">
          <i className="h-6 w-6 rounded-lg bg-accent-soft" />
          <i className="h-6 w-6 rounded-lg bg-tint-2" />
          <i className="h-6 w-6 rounded-lg bg-tint-2" />
          <i className="h-6 w-6 rounded-lg bg-tint-2" />
        </div>

        <div className="min-w-0 p-4 sm:p-5">
          <div className="text-[11.5px] font-bold text-muted">← Meetings</div>
          <h3 className="mt-1.5 truncate text-[15px] font-extrabold tracking-tight text-brand-dark">
            Q3 roadmap review
          </h3>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11.5px] text-muted">
            <span className="rounded-md bg-accent-soft px-2 py-0.5 font-bold text-accent-ink">Completed</span>
            <span>Google Meet · 18 Sept · 42 min</span>
          </div>

          <div className="mt-3.5 flex items-center gap-2.5 rounded-xl border border-line bg-sidebar px-3 py-2.5">
            <span className="btn-primary grid h-6 w-6 flex-none place-items-center rounded-full">
              <Play className="h-2.5 w-2.5 fill-current" />
            </span>
            <span aria-hidden="true" className="flex min-w-0 flex-1 items-center gap-px">
              {BARS.map((h, i) => (
                <i
                  key={i}
                  style={{ height: `${h}px` }}
                  className={`flex-1 rounded-[1px] ${i < 14 ? 'bg-brand-blue' : 'bg-tint-3'}`}
                />
              ))}
            </span>
            <span className="flex-none font-mono text-[10px] text-muted">12:04</span>
          </div>

          <div className="mt-3.5 flex gap-4 border-b border-line text-[11.5px] font-bold">
            <span className="-mb-px border-b-2 border-brand-blue pb-1.5 text-brand-dark">Summary</span>
            <span className="pb-1.5 text-muted">Action items 4</span>
            <span className="pb-1.5 text-muted">Transcript</span>
          </div>

          <div className="mt-3.5 rounded-xl border border-accent-line bg-accent-soft p-3">
            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-accent-ink">TL;DR</div>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-brand-dark">
              The team agreed to ship calendar sync first and revisit billing later. Priya owns the pricing copy;
              Dan will size the migration before Friday.
            </p>
          </div>
        </div>

        <div className="hidden flex-col gap-2.5 border-l border-line bg-sidebar p-3.5 min-[1000px]:flex">
          <span className="btn-primary max-w-[85%] self-end rounded-xl px-3 py-2 text-[11.5px] font-semibold">
            What did we decide about pricing?
          </span>
          <span className="max-w-[92%] rounded-xl border border-line bg-surface px-3 py-2 text-[11.5px] leading-relaxed text-body">
            You agreed to ship calendar sync first and come back to billing after.
            <span className="mt-1.5 block rounded-md bg-tint-2 px-1.5 py-0.5 font-mono text-[9.5px] text-muted">
              Q3 roadmap review — 18 Sept
            </span>
          </span>
          <span className="btn-primary max-w-[70%] self-end rounded-xl px-3 py-2 text-[11.5px] font-semibold">
            Who owns the migration?
          </span>
          <span className="flex w-fit gap-1 rounded-xl border border-line bg-surface px-3 py-2.5">
            <i className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" />
            <i className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" style={{ animationDelay: '0.15s' }} />
            <i className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" style={{ animationDelay: '0.3s' }} />
          </span>
        </div>
      </div>
    </div>

    {/* Fades the mock into the page rather than cutting it off with a hard edge. */}
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-linear-to-b from-transparent to-page"
    />
  </div>
);
