import React from 'react';
import { AlertCircle, Infinity as InfinityIcon } from 'lucide-react';

/**
 * How much of a plan limit has been used.
 *
 * Four decisions worth keeping:
 *
 * **Unlimited is not a bar.** A meter with no ceiling has nothing to fill, and
 * drawing an empty track next to "Unlimited" invites the reader to work out
 * what fraction it represents. It renders as a line of text instead.
 *
 * **Colour is a status, not a series.** The three tones come from the design
 * system's reserved status palette - the same tokens a meeting's state uses,
 * already defined for light and dark - rather than a new set of colours
 * invented here. Nothing about "meetings" versus "AI questions" changes the
 * colour; only how close to the ceiling it is does.
 *
 * **The state is never colour alone.** Every tone ships with the count in
 * words ("4 of 5 used") and, once it matters, an icon and a label too. A
 * colourblind reader, a printed page and a forced-colours theme all still say
 * the same thing.
 *
 * **Text wears ink tokens, never the fill colour.** The bar carries the
 * status; the numbers stay legible on their own terms.
 */
const TONES = {
  normal: { fill: 'bg-status-done-fg', track: 'bg-status-done-bg' },
  nearing: { fill: 'bg-status-processing-fg', track: 'bg-status-processing-bg' },
  reached: { fill: 'bg-status-failed-fg', track: 'bg-status-failed-bg' },
};

/** Warn from four fifths, which is late enough to mean something. */
function toneFor(used, limit) {
  if (used >= limit) return 'reached';
  if (used / limit >= 0.8) return 'nearing';
  return 'normal';
}

export const UsageMeter = ({ label, used, limit, unit }) => {
  // `null` is unlimited - see app/billing/plans.py. Never render it as 0.
  if (limit === null || limit === undefined) {
    return (
      <div className="flex items-center justify-between gap-3">
        <span className="text-[13.5px] text-body">{label}</span>
        <span className="inline-flex items-center gap-1.5 text-[13.5px] font-semibold text-muted">
          <InfinityIcon className="h-3.5 w-3.5" aria-hidden="true" />
          Unlimited
        </span>
      </div>
    );
  }

  const tone = toneFor(used, limit);
  const { fill, track } = TONES[tone];
  // Clamped: usage can exceed a limit when a plan is downgraded mid-period,
  // and a bar wider than its track would break the layout rather than tell
  // anyone anything.
  const pct = Math.min(100, Math.round((used / limit) * 100));

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13.5px] text-body">{label}</span>
        <span className="text-[13.5px] font-semibold text-brand-dark">
          {used} of {limit}
          {unit ? ` ${unit}` : ''}
        </span>
      </div>

      <div
        className={`h-1.5 w-full overflow-hidden rounded-full ${track}`}
        role="progressbar"
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-label={`${label}: ${used} of ${limit} used`}
      >
        {/* Rounded at both ends and anchored to the start, so a tiny value is
            still a visible mark rather than a sliver. */}
        <div
          className={`h-full rounded-full transition-[width] duration-300 ${fill}`}
          style={{ width: `${Math.max(pct, used > 0 ? 4 : 0)}%` }}
        />
      </div>

      {tone !== 'normal' && (
        <span
          className={[
            'inline-flex items-center gap-1.5 text-[12.5px]',
            tone === 'reached' ? 'text-status-failed-fg' : 'text-status-processing-fg',
          ].join(' ')}
        >
          <AlertCircle className="h-3.5 w-3.5 flex-none" aria-hidden="true" />
          {tone === 'reached'
            ? 'Limit reached — upgrade to continue'
            : `${limit - used} left this period`}
        </span>
      )}
    </div>
  );
};
