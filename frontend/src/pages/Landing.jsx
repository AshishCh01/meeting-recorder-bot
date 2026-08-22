import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Menu, X, FileText, MessageCircleQuestion, Search, ShieldOff } from 'lucide-react';
import { ThemeToggle } from '../components/ThemeToggle';
import meetiqLogo from '../assets/logo.png';

const STEPS = [
  {
    n: '1',
    title: 'Paste the link',
    body: 'Drop in a Google Meet or Zoom URL to get started. Calendar sync is coming soon.',
  },
  {
    n: '2',
    title: 'The bot joins',
    body: 'It shows up under a name you choose, records audio only, and leaves when the call ends.',
  },
  {
    n: '3',
    title: 'Get your notes',
    body: 'Transcript, summary, and action items in a couple of minutes — then ask the AI anything.',
  },
];

const FEATURES = [
  {
    icon: FileText,
    title: 'Summary and action items',
    body: 'Every recording comes back with a TL;DR, key points, and owners on the follow-ups — not a wall of transcript.',
  },
  {
    icon: MessageCircleQuestion,
    title: 'Ask the meeting anything',
    body: 'A chat that answers from the transcript and cites the timestamp, so you can check it before you quote it.',
  },
  {
    icon: Search,
    title: 'Searchable transcripts',
    body: 'Speaker-labelled, timestamped, and searchable across every call you have recorded.',
  },
  {
    icon: ShieldOff,
    title: 'Off by default',
    body: 'The bot only joins meetings you turn on. No engagement scores, no participant emails, no video stored — ever.',
  },
];

const PLANS = [
  {
    name: 'Free',
    price: '$0',
    period: 'forever',
    features: ['5 meetings a month', '30-minute recording cap', 'Transcript + summary', 'Delete anything, anytime'],
    cta: 'Start free',
    highlight: false,
  },
  {
    name: 'Pro',
    price: '$8',
    period: '/month',
    features: [
      'Unlimited meetings',
      '4-hour recording cap',
      'Action items with owners',
      'AI chat with citations',
      'Markdown / PDF export',
    ],
    cta: 'Start 14-day trial',
    highlight: true,
  },
  {
    name: 'Team',
    price: '$6',
    period: '/user/month',
    features: ['Everything in Pro', 'Shared meeting workspace', 'Admin controls + SSO', 'Retention policies'],
    cta: 'Talk to us',
    highlight: false,
  },
];

const FOOTER_COLUMNS = [
  { title: 'PRODUCT', links: ['How it works', 'Pricing', 'Changelog', 'vs. Otter'] },
  { title: 'PRIVACY', links: ['What we store', 'Data deletion', 'Subprocessors', 'DPA'] },
  { title: 'COMPANY', links: ['About', 'Contact', 'Status', 'Terms'] },
];

export const Landing = () => {
  const [email, setEmail] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();

  const goToSignup = (e) => {
    e.preventDefault();
    navigate(email.trim() ? `/register?email=${encodeURIComponent(email.trim())}` : '/register');
  };

  return (
    <div className="min-h-screen bg-page">
      {/* Header */}
      <header className="sticky top-0 z-40 bg-surface/90 backdrop-blur-sm border-b border-line">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-5 sm:px-6 h-16">
          <div className="flex items-center gap-2.5">
            <img src={meetiqLogo} alt="MeetIQ" className="h-7 w-7 object-contain" />
            <span className="text-lg font-extrabold text-brand-dark tracking-tight">MeetIQ</span>
          </div>

          <nav className="hidden md:flex items-center gap-8">
            <a href="#how-it-works" className="text-sm font-semibold text-body hover:text-brand-dark transition-colors">
              How it works
            </a>
            <a href="#pricing" className="text-sm font-semibold text-body hover:text-brand-dark transition-colors">
              Pricing
            </a>
            <span className="text-sm font-semibold text-body">Privacy</span>
          </nav>

          <div className="hidden md:flex items-center gap-3">
            <ThemeToggle className="p-2 text-muted hover:text-brand-dark transition-colors" iconClassName="h-5 w-5" />
            <Link to="/login" className="text-sm font-semibold text-brand-dark px-2">
              Sign in
            </Link>
            <Link
              to="/register"
              className="px-4 py-2 rounded-lg bg-linear-to-br from-brand-blue to-brand-blue-light text-white text-sm font-bold hover:opacity-90 transition-opacity"
            >
              Start free
            </Link>
          </div>

          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="md:hidden p-2 text-brand-dark"
            aria-label="Toggle menu"
          >
            {menuOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
          </button>
        </div>

        {menuOpen && (
          <div className="md:hidden border-t border-line px-5 py-4 flex flex-col gap-4 bg-surface">
            <a href="#how-it-works" onClick={() => setMenuOpen(false)} className="text-sm font-semibold text-body">
              How it works
            </a>
            <a href="#pricing" onClick={() => setMenuOpen(false)} className="text-sm font-semibold text-body">
              Pricing
            </a>
            <span className="text-sm font-semibold text-body">Privacy</span>
            <div className="flex items-center justify-between pt-2 border-t border-line">
              <Link to="/login" onClick={() => setMenuOpen(false)} className="text-sm font-bold text-brand-dark">
                Sign in
              </Link>
              <ThemeToggle className="p-2 text-muted" iconClassName="h-5 w-5" />
            </div>
            <Link
              to="/register"
              onClick={() => setMenuOpen(false)}
              className="text-center px-4 py-3 rounded-lg bg-linear-to-br from-brand-blue to-brand-blue-light text-white text-sm font-bold"
            >
              Start free
            </Link>
          </div>
        )}
      </header>

      {/* Hero */}
      <section className="max-w-6xl mx-auto px-5 sm:px-6 pt-12 pb-16 md:pt-20 md:pb-20 grid md:grid-cols-[1.05fr_.95fr] gap-10 md:gap-14 items-center">
        <div className="flex flex-col gap-5 md:gap-6">
          <span className="self-start inline-flex items-center px-3 py-1.5 rounded-full bg-status-done-bg text-status-done-fg text-xs font-bold">
            Records only when you ask it to
          </span>
          <h1 className="text-[34px] md:text-[58px] leading-[1.05] md:leading-[1.02] tracking-[-0.03em] md:tracking-[-0.035em] font-extrabold text-brand-dark text-balance">
            Meeting notes, not meeting surveillance.
          </h1>
          <p className="text-[15.5px] md:text-[19px] leading-[1.55] text-body max-w-xl text-pretty">
            A bot joins your Google Meet or Zoom, records the audio, and hands back a transcript, a summary, action
            items, and an AI you can ask questions. No engagement scores. No emails to your guests.{' '}
            <strong className="text-brand-dark">$8 a month</strong>, flat.
          </p>
          <form onSubmit={goToSignup} className="flex flex-col sm:flex-row gap-2.5 max-w-md">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              className="flex-1 h-12.5 px-4 rounded-[11px] border border-border bg-surface text-[15px] text-brand-dark placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-blue/20 focus:border-brand-blue transition-all"
            />
            <button
              type="submit"
              className="h-12.5 px-6 rounded-[11px] bg-linear-to-br from-brand-blue to-brand-blue-light text-white text-[15px] font-bold hover:opacity-90 transition-opacity shrink-0"
            >
              Start free
            </button>
          </form>
          <div className="text-[13.5px] text-muted">5 meetings a month free · no card · cancel in two clicks</div>
        </div>

        <div className="flex flex-col gap-3">
          <div className="h-50 md:h-82.5 rounded-2xl border border-border-strong bg-[repeating-linear-gradient(135deg,var(--color-line)_0_10px,var(--color-border-strong)_10px_20px)] flex items-center justify-center">
            <span className="font-mono text-xs text-muted bg-surface px-3 py-1.5 rounded-md border border-border-strong">
              product shot — meeting detail
            </span>
          </div>
          <div className="hidden md:grid grid-cols-3 gap-3">
            <div className="p-3.5 border border-border-strong rounded-xl">
              <div className="text-[15px] font-extrabold text-brand-dark">Audio only</div>
              <div className="text-xs text-muted mt-1">no video stored</div>
            </div>
            <div className="p-3.5 border border-border-strong rounded-xl">
              <div className="text-[15px] font-extrabold text-brand-dark">No auto-email</div>
              <div className="text-xs text-muted mt-1">guests get nothing</div>
            </div>
            <div className="p-3.5 border border-border-strong rounded-xl">
              <div className="text-[15px] font-extrabold text-brand-dark">Delete anytime</div>
              <div className="text-xs text-muted mt-1">audio + transcript</div>
            </div>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how-it-works" className="bg-sidebar border-y border-line py-14 md:py-16">
        <div className="max-w-6xl mx-auto px-5 sm:px-6">
          <div className="font-mono text-xs tracking-[0.1em] text-brand-blue">HOW IT WORKS</div>
          <h2 className="mt-3 mb-8 md:mb-10 text-2xl md:text-[32px] font-extrabold tracking-tight text-brand-dark">
            Three steps, about twenty seconds.
          </h2>
          <div className="grid md:grid-cols-3 gap-4 md:gap-5">
            {STEPS.map((s) => (
              <div key={s.n} className="bg-surface border border-border-strong rounded-2xl p-5 flex flex-col gap-2.5">
                <span className="w-7.5 h-7.5 rounded-lg bg-status-done-bg text-status-done-fg text-sm font-extrabold flex items-center justify-center">
                  {s.n}
                </span>
                <div className="text-lg font-extrabold text-brand-dark tracking-tight">{s.title}</div>
                <div className="text-[14.5px] leading-relaxed text-body">{s.body}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-14 md:py-16">
        <div className="max-w-6xl mx-auto px-5 sm:px-6 grid sm:grid-cols-2 gap-4 md:gap-5">
          {FEATURES.map((f) => (
            <div key={f.title} className="border border-border-strong rounded-2xl p-5 md:p-6.5 flex gap-4">
              <span className="flex-none w-9.5 h-9.5 rounded-[10px] bg-linear-to-br from-brand-blue to-brand-blue-light flex items-center justify-center">
                <f.icon className="w-5 h-5 text-white" />
              </span>
              <div className="flex flex-col gap-1.5">
                <div className="text-lg font-extrabold text-brand-dark tracking-tight">{f.title}</div>
                <div className="text-[15px] leading-relaxed text-body text-pretty">{f.body}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="bg-sidebar border-y border-line py-14 md:py-16">
        <div className="max-w-6xl mx-auto px-5 sm:px-6">
          <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-2 mb-7 md:mb-8">
            <div>
              <div className="font-mono text-xs tracking-[0.1em] text-brand-blue">PRICING</div>
              <h2 className="mt-3 text-2xl md:text-[32px] font-extrabold tracking-tight text-brand-dark">
                Half the price of the engagement-score crowd.
              </h2>
            </div>
            <div className="text-sm text-muted">Billed monthly · cancel anytime</div>
          </div>

          <div className="grid md:grid-cols-3 gap-5">
            {PLANS.map((plan) => (
              <div
                key={plan.name}
                className={`relative bg-surface rounded-2xl p-6.5 flex flex-col gap-4.5 ${
                  plan.highlight
                    ? 'border-2 border-brand-blue shadow-[0_12px_30px_rgba(11,79,180,0.12)]'
                    : 'border border-border-strong'
                }`}
              >
                {plan.highlight && (
                  <span className="absolute -top-3 left-7 px-3 py-1 rounded-full bg-linear-to-br from-brand-blue to-brand-blue-light text-white text-[11.5px] font-extrabold tracking-wide">
                    MOST POPULAR
                  </span>
                )}
                <div className="text-[15px] font-extrabold text-brand-dark">{plan.name}</div>
                <div className="flex items-end gap-1.5">
                  <span className="text-4xl font-extrabold tracking-tight text-brand-dark">{plan.price}</span>
                  <span className="text-sm text-muted pb-1.5">{plan.period}</span>
                </div>
                <div className="h-px bg-line" />
                <ul className="flex flex-col gap-2.5">
                  {plan.features.map((f) => (
                    <li key={f} className="flex gap-2.5 text-[14.5px] text-body leading-relaxed">
                      <span className="text-brand-blue font-extrabold">·</span>
                      {f}
                    </li>
                  ))}
                </ul>
                <Link
                  to="/register"
                  className={`mt-auto text-center py-3 rounded-[10px] text-[14.5px] font-bold transition-opacity hover:opacity-90 ${
                    plan.highlight
                      ? 'bg-linear-to-br from-brand-blue to-brand-blue-light text-white'
                      : 'border border-border text-brand-dark'
                  }`}
                >
                  {plan.cta}
                </Link>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-[#0F1720] px-5 sm:px-6 py-12 md:py-14">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row justify-between gap-10">
          <div className="flex flex-col gap-3 max-w-xs">
            <div className="flex items-center gap-2.5">
              <img src={meetiqLogo} alt="" className="h-6.5 w-6.5 object-contain" />
              <span className="text-base font-extrabold text-white">MeetIQ</span>
            </div>
            <div className="text-[13.5px] leading-relaxed text-[#8A97A4]">
              Audio-only meeting notes for people who don't want their calls scored.
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-8 sm:gap-10">
            {FOOTER_COLUMNS.map((col) => (
              <div key={col.title} className="flex flex-col gap-2.5">
                <div className="text-xs font-extrabold tracking-[0.08em] text-[#5B6875]">{col.title}</div>
                {col.links.map((l) => (
                  <div key={l} className="text-[13.5px] text-[#C4CDD5]">
                    {l}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
        <div className="max-w-6xl mx-auto mt-10 pt-6 border-t border-white/10 text-xs text-[#5B6875]">
          © 2026 MeetIQ
        </div>
      </footer>
    </div>
  );
};
