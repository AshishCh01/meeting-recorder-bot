import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Menu, X, ArrowRight, Check, Plus, Sparkles, FileText, AudioLines, CalendarDays,
} from 'lucide-react';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/Logo';
import { Reveal } from '../components/landing/Reveal';
import { ProductMock } from '../components/landing/ProductMock';

const NAV_LINKS = [
  { href: '#how-it-works', label: 'How it works' },
  { href: '#features', label: 'Features' },
  { href: '#pricing', label: 'Pricing' },
  { href: '#faq', label: 'FAQ' },
];

const STEPS = [
  {
    n: '1',
    title: 'Paste the link',
    body: 'Drop in a Google Meet or Zoom URL, or opt in a single event from your connected calendar.',
  },
  {
    n: '2',
    title: 'The bot joins',
    body: 'It appears under a name you choose, records audio only, and leaves when the call ends.',
  },
  {
    n: '3',
    title: 'Get your notes',
    body: 'Transcript, summary and action items in a couple of minutes — then ask the AI anything.',
  },
];

const PROMISES = [
  { title: 'Audio only', body: 'No video is ever captured or stored.' },
  { title: 'Off by default', body: 'The bot joins only the meetings you turn on.' },
  { title: 'No auto-emails', body: 'Your guests get nothing from us, ever.' },
  { title: 'Delete any time', body: 'One click removes the audio, transcript and chat.' },
];

// Every answer here has to match what the product actually does today.
const FAQ = [
  {
    q: 'Does MeetIQ record video?',
    a: 'No. The bot captures audio only. No video is recorded or stored at any point.',
  },
  {
    q: 'Do my guests get an email?',
    a: 'Never. MeetIQ sends nothing to anyone in the call. The notes go to you and stay with you.',
  },
  {
    q: "Do I need to tell people they're being recorded?",
    a: 'Yes. The bot joins as a visible participant under a name you choose, so everyone can see it is there. Recording laws vary by country, so tell participants and get their agreement before you start.',
  },
  {
    q: 'Which platforms are supported?',
    a: 'Google Meet and Zoom today, from a pasted link or a synced Google Calendar event.',
  },
  {
    q: 'Can I delete a recording?',
    a: 'Yes. Deleting a meeting removes the audio, the transcript and the chat history together.',
  },
  {
    q: 'Will the bot join meetings on its own?',
    a: 'Only for calendar events you switch on one by one. Connecting your calendar shows your upcoming meetings; it does not record any of them.',
  },
];

const SectionHead = ({ eyebrow, title, children, centered = false }) => (
  <Reveal className={`max-w-2xl ${centered ? 'mx-auto text-center' : ''}`}>
    <div className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted">{eyebrow}</div>
    <h2 className="mt-2.5 text-[clamp(1.6rem,3.5vw,2.25rem)] font-extrabold leading-[1.15] tracking-[-0.03em] text-brand-dark">
      {title}
    </h2>
    {children && <p className="mt-3 text-[15px] leading-relaxed text-body">{children}</p>}
  </Reveal>
);

const Tile = ({ icon: Icon, title, children, demo, wide = false, delay = 0 }) => (
  <Reveal
    as="article"
    delay={delay}
    className={`flex min-w-0 flex-col rounded-2xl border border-line bg-surface p-6 ${wide ? 'md:col-span-2' : ''}`}
  >
    <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-soft text-accent-ink">
      <Icon className="h-[19px] w-[19px]" />
    </span>
    <h3 className="mt-4 text-[17px] font-extrabold tracking-tight text-brand-dark">{title}</h3>
    <p className="mt-2 text-[14.5px] leading-relaxed text-body">{children}</p>
    {demo && <div className="mt-4 flex flex-col gap-2">{demo}</div>}
  </Reveal>
);

export const Landing = () => {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="min-h-screen scroll-smooth bg-page motion-reduce:scroll-auto">
      <header className="sticky top-0 z-40 border-b border-line bg-page/85 backdrop-blur-lg">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-5 sm:px-6">
          <a href="#top" className="flex items-center gap-2.5">
            <Logo alt="MeetIQ" className="h-7 w-7" />
            <span className="text-lg font-extrabold tracking-tight text-brand-dark">MeetIQ</span>
          </a>

          <nav className="hidden items-center gap-7 md:flex">
            {NAV_LINKS.map((l) => (
              <a key={l.href} href={l.href} className="text-sm font-semibold text-body transition-colors hover:text-brand-dark">
                {l.label}
              </a>
            ))}
          </nav>

          <div className="hidden items-center gap-2 md:flex">
            <ThemeToggle
              className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] text-muted transition-colors hover:bg-tint-2 hover:text-brand-dark"
              iconClassName="h-4.5 w-4.5"
            />
            <Link to="/login" className="px-2 text-sm font-semibold text-brand-dark">
              Sign in
            </Link>
            <Link to="/register" className="btn-primary rounded-[10px] px-4 py-2 text-sm font-bold transition-opacity hover:opacity-90">
              Start free
            </Link>
          </div>

          <button
            onClick={() => setMenuOpen((v) => !v)}
            aria-expanded={menuOpen}
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] text-brand-dark md:hidden"
          >
            {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>

        {menuOpen && (
          <div className="border-t border-line bg-page px-5 py-4 md:hidden">
            <nav className="flex flex-col gap-1">
              {NAV_LINKS.map((l) => (
                <a
                  key={l.href}
                  href={l.href}
                  onClick={() => setMenuOpen(false)}
                  className="rounded-lg px-2 py-2.5 text-[15px] font-semibold text-body hover:bg-tint-2 hover:text-brand-dark"
                >
                  {l.label}
                </a>
              ))}
            </nav>
            <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
              <Link to="/login" className="flex-1 rounded-[10px] border border-border py-2.5 text-center text-sm font-bold text-brand-dark">
                Sign in
              </Link>
              <Link to="/register" className="btn-primary flex-1 rounded-[10px] py-2.5 text-center text-sm font-bold">
                Start free
              </Link>
              <ThemeToggle
                className="inline-flex h-10 w-10 flex-none items-center justify-center rounded-[10px] border border-border text-muted"
                iconClassName="h-4.5 w-4.5"
              />
            </div>
          </div>
        )}
      </header>

      <main id="top">
        {/* ------------------------------------------------------- hero */}
        <section className="relative overflow-hidden pt-16 sm:pt-20">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -top-40 left-1/2 h-100 w-200 -translate-x-1/2 rounded-full bg-accent-soft blur-[80px]"
          />
          <div className="relative mx-auto max-w-3xl px-5 text-center sm:px-6">
            <Reveal as="span" className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-[12.5px] font-semibold text-body">
              <i className="h-1.5 w-1.5 rounded-full bg-brand-blue" />
              Now with Google Calendar sync
            </Reveal>

            <Reveal as="h1" delay={60} className="mt-5 text-[clamp(2.1rem,6vw,3.4rem)] font-extrabold leading-[1.08] tracking-[-0.035em] text-brand-dark">
              Turn every meeting into{' '}
              <span className="bg-linear-to-br from-brand-blue to-brand-blue-light bg-clip-text text-transparent">
                knowledge.
              </span>
            </Reveal>

            <Reveal as="p" delay={120} className="mx-auto mt-5 max-w-xl text-[16.5px] leading-relaxed text-body">
              MeetIQ joins your calls, records the audio, and hands back searchable transcripts, summaries and
              action items you can act on.
            </Reveal>

            <Reveal delay={180} className="mt-8 flex flex-wrap items-center justify-center gap-2.5">
              <Link to="/register" className="btn-primary inline-flex h-12 items-center gap-2 rounded-xl px-5 text-[15px] font-bold transition-opacity hover:opacity-90">
                Start free
                <ArrowRight className="h-4 w-4" />
              </Link>
              {/* Scrolls to the section. No play icon - there is no video. */}
              <a href="#how-it-works" className="inline-flex h-12 items-center rounded-xl border border-border px-5 text-[15px] font-bold text-brand-dark transition-colors hover:bg-tint-2">
                See how it works
              </a>
            </Reveal>

            <Reveal as="p" delay={240} className="mt-5 text-[13.5px] text-muted">
              Free while in beta · no card required · audio only, never video
            </Reveal>
          </div>

          <Reveal delay={300}>
            <ProductMock />
          </Reveal>
        </section>

        {/* ------------------------------------------------- works with */}
        <div className="border-y border-line bg-sidebar py-7">
          <Reveal className="mx-auto flex max-w-6xl flex-wrap items-center justify-center gap-2.5 px-5 sm:px-6">
            <span className="w-full text-center font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted">
              Works with
            </span>
            {['Google Meet', 'Zoom', 'Google Calendar', 'PDF export'].map((c) => (
              <span key={c} className="rounded-full border border-line bg-surface px-3.5 py-1.5 text-[13px] font-semibold text-body">
                {c}
              </span>
            ))}
          </Reveal>
        </div>

        {/* ----------------------------------------------- how it works */}
        <section id="how-it-works" className="scroll-mt-20 py-20">
          <div className="mx-auto max-w-6xl px-5 sm:px-6">
            <SectionHead eyebrow="How it works" title="Three steps, about twenty seconds.">
              Paste a link or pick an event from your calendar. MeetIQ does the rest and stays out of the call.
            </SectionHead>

            <div className="mt-12 grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-[repeat(3,minmax(0,1fr))]">
              {STEPS.map((s, i) => (
                <Reveal as="article" key={s.n} delay={i * 90} className="rounded-2xl border border-line bg-surface p-6">
                  <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent-soft font-mono text-[13px] font-bold text-accent-ink">
                    {s.n}
                  </span>
                  <h3 className="mt-4 text-[17px] font-extrabold tracking-tight text-brand-dark">{s.title}</h3>
                  <p className="mt-2 text-[14.5px] leading-relaxed text-body">{s.body}</p>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* --------------------------------------------------- features */}
        <section id="features" className="scroll-mt-20 py-20">
          <div className="mx-auto max-w-6xl px-5 sm:px-6">
            <SectionHead eyebrow="Features" title="Everything that was said, in a form you can use.">
              Not a wall of transcript. Summaries you can skim, follow-ups with owners, and an AI that cites its
              sources.
            </SectionHead>

            <div className="mt-12 grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-[repeat(2,minmax(0,1fr))]">
              <Tile
                icon={Sparkles}
                wide
                title="Ask across every meeting you've recorded"
                demo={
                  <>
                    <span className="btn-primary max-w-[62%] self-end rounded-xl px-3.5 py-2 text-[13px] font-semibold">
                      Which meeting discussed the migration?
                    </span>
                    <span className="max-w-[78%] rounded-xl border border-line bg-page px-3.5 py-2 text-[13px] leading-relaxed text-body">
                      Two calls covered it.{' '}
                      <span className="rounded-md bg-tint-2 px-1.5 py-0.5 font-mono text-[10px] text-muted">
                        Q3 roadmap review — 18 Sept
                      </span>
                    </span>
                  </>
                }
              >
                &quot;What did we decide about pricing last month?&quot; Answers come back with a link to the meeting
                they came from, so you can check the source before you quote it.
              </Tile>

              <Tile
                icon={FileText}
                delay={90}
                title="Summary and action items"
                demo={
                  <>
                    {[['Send the pricing draft', 'Priya'], ['Size the migration', 'Dan']].map(([task, who]) => (
                      <span key={task} className="flex items-center gap-2.5 rounded-xl border border-line bg-page px-3.5 py-2 text-[13px] text-body">
                        <i className="h-3.5 w-3.5 flex-none rounded-[4px] border-[1.5px] border-border" />
                        <span className="min-w-0 flex-1 truncate">{task}</span>
                        <span className="flex-none rounded-full bg-tint-2 px-2 py-0.5 text-[11px] font-bold">{who}</span>
                      </span>
                    ))}
                  </>
                }
              >
                A TL;DR, the key points, and follow-ups with an owner and a timestamp attached.
              </Tile>

              <Tile
                icon={AudioLines}
                delay={40}
                title="Speaker-labelled transcripts"
                demo={
                  <>
                    {[['12:04', 'Dan', 'Can we ship sync first?'], ['12:11', 'Priya', 'Yes, billing can wait.']].map(
                      ([ts, who, said]) => (
                        <span key={ts} className="flex gap-2.5 rounded-xl border border-line bg-page px-3.5 py-2 text-[13px] text-body">
                          <i className="flex-none font-mono text-[11px] not-italic text-muted">{ts}</i>
                          <span className="min-w-0">
                            <b className="font-bold text-brand-dark">{who}:</b> {said}
                          </span>
                        </span>
                      )
                    )}
                  </>
                }
              >
                Every line timestamped and attributed, with the audio next to it so you can jump straight there.
              </Tile>

              <Tile
                icon={CalendarDays}
                wide
                delay={120}
                title="Calendar sync that stays off by default"
                demo={
                  <>
                    <span className="flex items-center justify-between gap-3 rounded-xl border border-line bg-page px-3.5 py-2 text-[13px] text-body">
                      <span className="min-w-0 truncate">Design review · Tue, 10:00 · Google Meet</span>
                      <span className="flex-none rounded-md bg-accent-soft px-2 py-0.5 text-[11px] font-bold text-accent-ink">
                        Scheduled
                      </span>
                    </span>
                    <span className="flex items-center justify-between gap-3 rounded-xl border border-line bg-page px-3.5 py-2 text-[13px] text-muted opacity-70">
                      <span className="min-w-0 truncate">Standup · Wed, 09:30 · Google Meet</span>
                      <span className="flex-none rounded-md bg-tint-2 px-2 py-0.5 text-[11px] font-bold">Off</span>
                    </span>
                  </>
                }
              >
                Connect Google Calendar and your upcoming calls appear in one list. Nothing is recorded until you
                switch on that specific event — MeetIQ joins a couple of minutes before it starts.
              </Tile>
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------- privacy */}
        <section className="py-20">
          <div className="mx-auto max-w-6xl px-5 sm:px-6">
            <SectionHead eyebrow="Privacy" title="Notes, not surveillance.">
              No engagement scores. No emails to your guests. Nothing recorded unless you ask for it.
            </SectionHead>

            <div className="mt-12 grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-[repeat(2,minmax(0,1fr))] lg:grid-cols-[repeat(4,minmax(0,1fr))]">
              {PROMISES.map((p, i) => (
                <Reveal as="article" key={p.title} delay={i * 60} className="rounded-2xl border border-line bg-surface p-5">
                  <span className="grid h-7 w-7 place-items-center rounded-full bg-accent-soft text-accent-ink">
                    <Check className="h-[15px] w-[15px]" strokeWidth={2.6} />
                  </span>
                  <h3 className="mt-3.5 text-[15px] font-extrabold tracking-tight text-brand-dark">{p.title}</h3>
                  <p className="mt-1.5 text-[13.5px] leading-relaxed text-body">{p.body}</p>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------- pricing */}
        <section id="pricing" className="scroll-mt-20 py-20">
          <div className="mx-auto max-w-6xl px-5 sm:px-6">
            {/* No tiers and no prices: billing does not exist yet, so three
                cards with buttons would be three buttons leading nowhere. */}
            <Reveal className="mx-auto max-w-2xl rounded-3xl border border-accent-line bg-accent-soft p-8 text-center sm:p-12">
              <div className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-accent-ink">Pricing</div>
              <h2 className="mt-2.5 text-[clamp(1.6rem,3.5vw,2.25rem)] font-extrabold leading-[1.15] tracking-[-0.03em] text-brand-dark">
                Free while MeetIQ is in beta.
              </h2>
              <p className="mx-auto mt-3 max-w-md text-[15px] leading-relaxed text-body">
                Every account gets transcripts, summaries, action items and Ask AI, at no cost and with no card.
                Paid plans will come later — we will tell you long before anything changes.
              </p>
              <Link
                to="/register"
                className="btn-primary mt-7 inline-flex h-12 items-center gap-2 rounded-xl px-6 text-[15px] font-bold transition-opacity hover:opacity-90"
              >
                Create your account
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Reveal>
          </div>
        </section>

        {/* -------------------------------------------------------- faq */}
        <section id="faq" className="scroll-mt-20 py-20">
          <div className="mx-auto max-w-3xl px-5 sm:px-6">
            <SectionHead eyebrow="FAQ" title="Questions people ask first." centered />

            <Reveal className="mt-12 overflow-hidden rounded-2xl border border-line bg-surface">
              {FAQ.map((item, i) => (
                <details key={item.q} open={i === 0} className="group border-b border-line last:border-b-0">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 text-[15px] font-bold text-brand-dark marker:hidden">
                    {item.q}
                    <span className="grid h-6 w-6 flex-none place-items-center rounded-md bg-tint-2 text-muted transition-transform group-open:rotate-45 motion-reduce:transition-none">
                      <Plus className="h-3.5 w-3.5" strokeWidth={2.6} />
                    </span>
                  </summary>
                  <p className="px-5 pb-4 text-[14.5px] leading-relaxed text-body">{item.a}</p>
                </details>
              ))}
            </Reveal>
          </div>
        </section>

        {/* -------------------------------------------------------- cta */}
        <section className="px-5 pb-20 sm:px-6">
          <Reveal className="mx-auto max-w-4xl rounded-3xl border border-line bg-surface p-10 text-center sm:p-14">
            <h2 className="text-[clamp(1.6rem,3.5vw,2.25rem)] font-extrabold leading-[1.15] tracking-[-0.03em] text-brand-dark">
              Your next meeting can write itself up.
            </h2>
            <p className="mx-auto mt-3 max-w-lg text-[15px] leading-relaxed text-body">
              Paste a link and see what comes back. Free while MeetIQ is in beta, and no card is required.
            </p>
            <div className="mt-7 flex flex-wrap items-center justify-center gap-2.5">
              <Link to="/register" className="btn-primary inline-flex h-12 items-center gap-2 rounded-xl px-5 text-[15px] font-bold transition-opacity hover:opacity-90">
                Start free
                <ArrowRight className="h-4 w-4" />
              </Link>
              <a href="#faq" className="inline-flex h-12 items-center rounded-xl border border-border px-5 text-[15px] font-bold text-brand-dark transition-colors hover:bg-tint-2">
                Read the FAQ
              </a>
            </div>
          </Reveal>
        </section>
      </main>

      <footer className="border-t border-line bg-sidebar py-12">
        <div className="mx-auto max-w-6xl px-5 sm:px-6">
          <div className="flex flex-col justify-between gap-8 md:flex-row">
            <div className="max-w-xs">
              <a href="#top" className="flex items-center gap-2.5">
                <Logo className="h-6 w-6" />
                <span className="text-base font-extrabold tracking-tight text-brand-dark">MeetIQ</span>
              </a>
              <p className="mt-3 text-[13.5px] leading-relaxed text-muted">
                Audio-only meeting notes for people who don&apos;t want their calls scored.
              </p>
            </div>

            {/* Product and Company only. The privacy and terms pages land in
                phase 2; linking to them now would be four dead links, which
                is worse than not linking at all. */}
            <div className="flex gap-12">
              <div className="flex flex-col gap-2.5">
                <h3 className="text-[13px] font-extrabold text-brand-dark">Product</h3>
                {NAV_LINKS.map((l) => (
                  <a key={l.href} href={l.href} className="text-[13.5px] text-muted transition-colors hover:text-brand-dark">
                    {l.label}
                  </a>
                ))}
              </div>
              <div className="flex flex-col gap-2.5">
                <h3 className="text-[13px] font-extrabold text-brand-dark">Account</h3>
                <Link to="/login" className="text-[13.5px] text-muted transition-colors hover:text-brand-dark">
                  Sign in
                </Link>
                <Link to="/register" className="text-[13.5px] text-muted transition-colors hover:text-brand-dark">
                  Create an account
                </Link>
              </div>
            </div>
          </div>

          <div className="mt-10 flex flex-col gap-2 border-t border-line pt-6 text-[12.5px] text-muted sm:flex-row sm:justify-between">
            <span>© {new Date().getFullYear()} MeetIQ</span>
            <span>Built for people who&apos;d rather be in the conversation.</span>
          </div>
        </div>
      </footer>
    </div>
  );
};
