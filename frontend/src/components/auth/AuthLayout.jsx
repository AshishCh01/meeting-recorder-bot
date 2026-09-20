import React from 'react';
import { Link } from 'react-router-dom';
import { Check } from 'lucide-react';
import { Logo } from '../Logo';
import { ThemeToggle } from '../ThemeToggle';

// Everything here has to stay inside what the product actually does. No
// billing exists, so there is no quota to promise; no password reset flow
// exists, so there is no "Forgot password?" link.
const POINTS = [
  'Transcript, summary and action items a couple of minutes after the call',
  'Ask across every meeting you have recorded, with citations',
  'Audio only, no auto-emails to guests, delete anything any time',
];

/**
 * The shared auth frame: form on the left, brand panel on the right.
 *
 * The panel is hidden below 860px, where the form is the whole page - the
 * split is a desktop affordance, and on a phone a decorative column just
 * pushes the fields below the fold. 860 is a one-off boundary, so it is an
 * arbitrary variant rather than a named breakpoint nothing else would use.
 */
export const AuthLayout = ({ title, lede, children, footer }) => (
  <div className="grid min-h-dvh w-full grid-cols-1 bg-page min-[860px]:grid-cols-2">
    <div className="flex flex-col p-6">
      <header className="flex items-center justify-between">
        <Link to="/" className="flex items-center gap-2.5">
          <Logo alt="MeetIQ" className="h-6 w-6" />
          <span className="text-base font-extrabold tracking-tight text-brand-dark">MeetIQ</span>
        </Link>
        <ThemeToggle
          className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] text-muted transition-colors hover:bg-tint-2 hover:text-brand-dark"
          iconClassName="h-4.5 w-4.5"
        />
      </header>

      <div className="mx-auto my-auto w-full max-w-[372px] py-6">
        <h1 className="text-[26px] font-extrabold leading-tight tracking-tight text-brand-dark">{title}</h1>
        <p className="mt-2 text-[14.5px] text-muted">{lede}</p>
        {children}
        {footer && <p className="mt-6 text-center text-[13.5px] text-muted">{footer}</p>}
      </div>

      <p className="text-center text-[12.5px] text-muted">Audio only · nothing records until you ask</p>
    </div>

    <aside className="relative hidden flex-col justify-center gap-6 overflow-hidden border-l border-line bg-linear-160 from-surface-hover to-sidebar p-12 min-[860px]:flex">
      {/* A soft accent bloom behind the copy. Decorative, so it is hidden from
          assistive tech and sits under the content rather than over it. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-32 -top-32 h-95 w-130 rounded-full bg-accent-soft blur-[50px]"
      />
      <p className="relative max-w-[18ch] text-[23px] font-extrabold leading-[1.25] tracking-[-0.03em] text-brand-dark">
        Turn every meeting into knowledge.
      </p>
      <ul className="relative flex flex-col gap-3">
        {POINTS.map((point) => (
          <li key={point} className="flex gap-2.5 text-[13.5px] leading-relaxed text-body">
            <Check className="mt-0.5 h-4 w-4 flex-none text-brand-blue" strokeWidth={2.6} />
            {point}
          </li>
        ))}
      </ul>
    </aside>
  </div>
);
