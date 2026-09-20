# MeetIQ redesign prototype

Design prototypes, not production code. They exist so the look and the
layout can be settled before anything is rebuilt in React inside
`frontend/`.

**Nothing in `frontend/`, `backend/` or `meeting-bot/` is changed by this
folder.** Delete it and the app is unaffected.

## Viewing

No build step. Either open the files in a browser, or serve the folder:

```
npx serve prototype
```

| File | Screen |
| --- | --- |
| `index.html` | Landing page |
| `app/dashboard.html` | Meetings list |
| `app/meeting.html` | Meeting detail — summary, action items, transcript, Ask AI |
| `app/ask.html` | Ask AI across all meetings |
| `app/upcoming.html` | Calendar events, opted in one by one |
| `app/settings.html` | Account, bot, Ask AI, calendar, notifications |
| `app/login.html`, `app/register.html` | Sign in and sign up |

`app/app.css` holds the tokens, shell and shared components;
`app/screens.css` holds per-screen styles; `app/app.js` renders the shell
and the interactions. The shell lives in one place rather than being
copied into seven files — in the React port it is the Layout component.

## Design direction

- Visual reference: Attio (fine hairlines, restrained motion, ambient
  gradient). Writing reference: Cal.com (plain, short sentences).
- One palette across the landing page and the app. Change a token in
  `:root` and everything follows.
- Light and dark, on the same `dark` class and `theme` storage key the
  React app already uses, so a visitor's choice carries from the landing
  page into the app.

## What these prototypes fix

Each of these is a problem in the current app, not a style preference.

**Shell and navigation**
- Menu items are grouped into sections — Workspace, Intelligence, Account
  — instead of four ungrouped links above 250px of dead space.
- The sidebar carries a search/command palette (⌘K), item counts and a
  plan card, so the space is used.
- The active item is a quiet wash plus an edge marker, not a filled blue
  block.
- One account button opening a menu, instead of three stacked rows and a
  truncated email used as the identity.
- **Tablets get the icon rail, not the phone layout.** The current app
  switches at one breakpoint, so an iPad gets a stretched phone UI.

**Meetings list**
- Flat rows with dividers instead of a bordered card per row.
- The title gets the width; metadata is muted and secondary.
- A line of summary under each title, so the list is worth scanning.
- A status badge appears only when the status is *not* normal. Eight
  identical "Completed" badges carry no information.
- Row actions appear on hover, and are always visible on touch.
- Filter chips scroll sideways rather than being clipped at the edge.

**Meeting detail** — the screen with the real problem
- The header is one sticky line: back, title, metadata, actions. The title
  stays on one line instead of wrapping to three.
- The audio player is a slim bar pinned to the bottom of the column.
- One scroll container. No card inside a card, no nested scrollbar.
- On a 375px phone the content now gets **65% of the viewport**; in the
  current app the header, player and docked chat leave it roughly 40px.
- The Ask AI panel can be collapsed on desktop, and on phones opens as a
  sheet from a button instead of permanently docking over the content.
- Action items give the text the room; the owner and date no longer
  squeeze it into a one-word column.

**Ask AI**
- Full-bleed on phones. The current page puts the chat in a bordered card
  with its own scrollbar and clips the starter prompts mid-row.

**Everywhere**
- Inputs are 16px, so iOS Safari does not zoom the page on focus.
- Toasts instead of `window.alert()`.
- Tap targets are 44px on touch-sized screens.
- All motion collapses under `prefers-reduced-motion`.

## Checked, not assumed

Every screen was rendered in Chromium at 1440, 820, 375 and 320px, in both
themes. No horizontal overflow and no console errors at any size. The
command palette, account menu, rail collapse, row menus, record modal, tab
switching and the mobile Ask AI sheet were all clicked and verified.

## Not real yet

- The pricing tiers on the landing page (Free, Pro $5 for 30 recording
  hours, Pro Max $10 unlimited) are a proposal. There is no billing or
  usage metering in the codebase.
- Footer links to `/privacy`, `/what-we-store`, `/data-deletion` and
  `/terms` point at pages that do not exist yet. They are needed before
  launch, since the product records other people's voices.
- Data in the screens is representative sample content, not live.

## Carrying this into React

1. **Phase 0 is the token block** in `app/app.css`. Port it into
   `frontend/src/index.css` first; everything else depends on it.
2. `app/app.js` `renderShell()` maps to `components/Layout.jsx`, and `NAV`
   to the menu sections.
3. Redesign means JSX and CSS only. The hooks, `api` calls, routes and
   state in the current app work — streaming chat, status polling, retry
   and stop should not be rewritten to change how a screen looks.
4. Without JS these pages fall back to light mode regardless of the system
   setting, because the pre-paint script sets the class. The React app
   already behaves the same way.
