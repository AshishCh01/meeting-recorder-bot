import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle } from 'lucide-react';
import { Logo } from '../Logo';
import { ThemeToggle } from '../ThemeToggle';
import { DRAFT, LAST_UPDATED } from '../../pages/legal/company';

export const LEGAL_LINKS = [
  { to: '/privacy', label: 'Privacy policy' },
  { to: '/what-we-store', label: 'What we store' },
  { to: '/data-deletion', label: 'Data deletion' },
  { to: '/terms', label: 'Terms' },
];

// Prose primitives, so the four documents cannot drift apart typographically.
export const H2 = ({ children }) => (
  <h2 className="mt-10 text-[19px] font-extrabold tracking-tight text-brand-dark first:mt-0">{children}</h2>
);

export const P = ({ children }) => <p className="mt-3 text-[15px] leading-[1.7] text-body">{children}</p>;

export const UL = ({ children }) => (
  <ul className="mt-3 flex list-disc flex-col gap-2 pl-5 text-[15px] leading-[1.7] text-body marker:text-faint">
    {children}
  </ul>
);

export const Term = ({ children }) => <strong className="font-bold text-brand-dark">{children}</strong>;

/**
 * The shared frame for the four legal pages. They are prose, so this is
 * deliberately plain: one column at a comfortable measure, the same header and
 * footer as the landing page, and cross-links between the four so a reader who
 * lands on one can find the others.
 */
export const LegalPage = ({ title, summary, children }) => (
  <div className="min-h-screen bg-page">
    <header className="sticky top-0 z-40 border-b border-line bg-page/85 backdrop-blur-lg">
      <div className="mx-auto flex h-16 max-w-3xl items-center justify-between gap-4 px-5 sm:px-6">
        <Link to="/" className="flex items-center gap-2.5">
          <Logo alt="MeetIQ" className="h-7 w-7" />
          <span className="text-lg font-extrabold tracking-tight text-brand-dark">MeetIQ</span>
        </Link>
        <ThemeToggle
          className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] text-muted transition-colors hover:bg-tint-2 hover:text-brand-dark"
          iconClassName="h-4.5 w-4.5"
        />
      </div>
    </header>

    <main className="mx-auto max-w-3xl px-5 pb-20 pt-10 sm:px-6">
      <Link
        to="/"
        className="inline-flex items-center gap-1.5 text-[13px] font-bold text-muted transition-colors hover:text-brand-dark"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to MeetIQ
      </Link>

      <h1 className="mt-5 text-[clamp(1.75rem,4vw,2.25rem)] font-extrabold leading-[1.15] tracking-[-0.03em] text-brand-dark">
        {title}
      </h1>
      <p className="mt-2 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
        Last updated {LAST_UPDATED}
      </p>
      {summary && <p className="mt-4 text-[16px] leading-relaxed text-body">{summary}</p>}

      {DRAFT && (
        <div className="mt-6 flex items-start gap-3 rounded-xl border border-status-processing-fg/25 bg-status-processing-bg p-4">
          <AlertTriangle className="mt-0.5 h-4.5 w-4.5 flex-none text-status-processing-fg" />
          <p className="text-[13.5px] leading-relaxed text-status-processing-fg">
            <strong className="font-bold">Draft.</strong> This page describes how MeetIQ works today, but it has
            not been reviewed by a lawyer. Do not rely on it as a legal agreement until this notice is removed.
          </p>
        </div>
      )}

      <div className="mt-8">{children}</div>

      <nav aria-label="Legal pages" className="mt-14 flex flex-wrap gap-x-5 gap-y-2 border-t border-line pt-6">
        {LEGAL_LINKS.map((l) => (
          <Link key={l.to} to={l.to} className="text-[13.5px] text-muted transition-colors hover:text-brand-dark">
            {l.label}
          </Link>
        ))}
      </nav>
    </main>
  </div>
);
