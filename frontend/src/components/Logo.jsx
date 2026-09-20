import React, { useId } from 'react';

/**
 * The MeetIQ mark: a dot above a gradient arc. Replaces the logo.png raster,
 * so it stays sharp at any size and re-themes with the palette.
 *
 * Two things this has to get right:
 *   - fill and stop-color come from the .mark-* classes in index.css, never
 *     from presentation attributes. Browsers do not resolve var() inside an
 *     SVG presentation attribute, so fill="var(--color-brand-blue)" renders
 *     as nothing at all.
 *   - the gradient id is per-instance. The sidebar and the mobile top bar both
 *     render a mark on the same page, and duplicate ids mean every copy after
 *     the first resolves url(#id) to the first one's gradient - or, once that
 *     one unmounts, to nothing, leaving just the dot.
 *
 * `alt` mirrors the <img> it replaces: a string labels the mark, an empty
 * string hides it from assistive tech because a wordmark sits next to it.
 */
export const Logo = ({ className = 'h-8 w-8', alt = '' }) => {
  // useId is unique per instance but contains colons, which are awkward inside
  // url(#...); strip them down to an identifier that is safe everywhere.
  const gradientId = `meetiq-mark-${useId().replace(/[^a-zA-Z0-9]/g, '')}`;

  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      className={className}
      role={alt ? 'img' : undefined}
      aria-label={alt || undefined}
      aria-hidden={alt ? undefined : 'true'}
    >
      <circle className="mark-dot" cx="16" cy="6.6" r="3.6" />
      <path
        d="M5.6 20.2a10.4 10.4 0 0 1 20.8 0v3.4a4.8 4.8 0 0 1-4.8 4.8h-7.9"
        stroke={`url(#${gradientId})`}
        strokeWidth="4"
        strokeLinecap="round"
      />
      <defs>
        <linearGradient id={gradientId} x1="5" y1="10" x2="27" y2="29" gradientUnits="userSpaceOnUse">
          <stop className="mark-from" />
          <stop className="mark-to" offset="1" />
        </linearGradient>
      </defs>
    </svg>
  );
};
