# MeetIQ landing page prototype

A design prototype, not production code. It exists so the look can be settled
before any of it is rebuilt in React inside `frontend/`.

## Viewing it

It is one self-contained file with no build step. Either:

- open `prototype/index.html` in a browser, or
- serve the folder: `npx serve prototype`

## What this is

- **Phase 1 of the redesign**: landing page only. The app itself (dashboard,
  meeting page, Ask AI) is untouched.
- **Plain HTML and CSS**, deliberately. Iterating on the design is faster
  without React and Tailwind in the way, and the tokens at the top of the
  file port straight into `frontend/src/index.css` when the design is agreed.
- **Nothing in `frontend/`, `backend/` or `meeting-bot/` is changed by this
  folder.**

## Design direction

- Visual reference: Attio (fine hairlines, ambient gradient, restrained
  motion). Writing reference: Cal.com (plain, short sentences).
- All colours, spacing and radii come from the custom properties in `:root`.
  Change those and the whole page re-themes — the same approach the app
  already uses for dark mode.
- Motion: scroll reveals, a drifting hero glow, an animated waveform and a
  cursor spotlight on cards. Everything collapses under
  `prefers-reduced-motion: reduce`.

## Light and dark

Both themes ship. The page follows the system setting on a first visit, and
the moon/sun button in the header overrides that and remembers the choice.

- `:root` holds the light values, `.dark` on `<html>` redefines the same
  names. Every rule reads those variables and nothing else, so a colour is
  changed in one place, not two.
- **The switch matches the React app on purpose.** Same `dark` class and same
  `theme` storage key as `frontend/index.html` and `context/ThemeContext.jsx`.
  The app's Tailwind setup binds its `dark:` utilities to that class through
  `@custom-variant dark`, so one switch flips the tokens and every `dark:`
  utility together. Had this page used its own attribute, porting the tokens
  would have left the app half-themed. It also means a visitor's choice on
  the landing page carries into the app.
- An inline script in `<head>` resolves the theme before the first paint, so
  there is no flash of the wrong colours. It also adds a `js` class, which is
  what gates the scroll reveals — with scripting off the content is visible
  rather than stuck at `opacity: 0`.
- `localStorage` access is wrapped in `try/catch` for private windows.
- The logo mark themes with the page, via CSS classes rather than `fill`
  attributes: browsers do not resolve `var()` inside SVG presentation
  attributes.

One thing to carry into the React port: without JS this page falls back to
light regardless of the system setting, because the pre-paint script is what
sets the class. Fixing that means a second copy of the dark token list inside
`@media (prefers-color-scheme: dark)`, which risks the two drifting apart. The
React app already behaves the same way, so this is left as-is deliberately.

Checked in Chromium at 1440px and 375px in both themes — no horizontal
overflow, and the toggle flips, relabels itself and survives a reload.

## Claims on this page

Copy was kept to what the product actually does today: Google Meet and Zoom,
audio only, 90-minute recording cap, Google Calendar sync that is off until
you opt an event in, PDF export, Ask AI with citations, and full delete.

**The pricing tiers are a proposal, not a built feature.** There is no billing
in the codebase yet, and the Free limits (5 meetings a month), the Pro 30-hour
allowance and the Pro Max unlimited tier are not enforced anywhere. Either
build metering and billing before launch, or replace this section with a
"free while in beta" note.

## Still to do

- The footer's Privacy, What we store, Data deletion and Terms links point at
  routes that do not exist yet. These pages are needed before launch, since
  the product records other people's voices.
- "Sign in" and the plan buttons are placeholders.
- The product mock in the hero is built from HTML, not a screenshot. Once the
  app redesign lands, consider swapping it for a real screenshot or a short
  screen recording.
