# Frontend redesign — porting the prototype into `frontend/`

## Context

The current frontend works but looks generated: every element in its own rounded box, one blue used for buttons, links, badges and the active nav item alike, four ungrouped sidebar links above 250px of dead space, and a meeting page where the header and audio player take more than half the screen before any content. On a phone the meeting page leaves roughly 40px for the summary.

A full redesign was prototyped first: the landing page plus seven app screens, as static HTML and CSS.

> **Where the prototype lives.** The `prototype/` folder is on the
> **`frontend-redesign-phase-1`** branch, deliberately not on `main` — it ships
> with nothing and should never reach production. Check that branch out, or
> browse it on GitHub, to follow the `prototype/...` paths referenced below.
> Cut each phase branch from `main`, not from the prototype branch, so the
> folder is never merged in by accident.
 Design references are Attio (visuals) and Cal.com (copy). The prototypes were rendered in Chromium at 1440, 820, 375 and 320px in both themes, with no horizontal overflow and no console errors.

This plan ports that design into the React app in nine phases. It does not change what the app does.

## Rules (not negotiable)

| Rule | Detail |
|---|---|
| **The prototype is reference only** | Read `prototype/` for markup, tokens, spacing and behaviour, then write idiomatic React and Tailwind. **Do not copy the files into `frontend/`, do not import from `prototype/`, do not add it to the Vite build.** It stays a standalone folder that ships with nothing. |
| **Never touch `meeting-bot/`** | No exceptions. Its join flow is timing-sensitive and off-limits; see the note on join sleeps in the repo's history. If a frontend task appears to need a change there, stop and ask. |
| **Do not touch `backend/`** | Backend edits are allowed **only if a phase genuinely cannot be completed without one** — for example an endpoint returning a field the new UI must display. Then: backend changes go in their own commit, stay as small as possible, touch no other feature, and are called out in the PR description. Styling is never a reason to edit the backend. |
| **Redesign means JSX and CSS only** | Do not rewrite hooks, `api` calls, routes, context or state. Streaming chat, status polling, retry, stop, calendar sync and PDF export all work today. A redesign that breaks them has failed, however good it looks. |
| **One branch and one PR per phase** | Reviewable, revertable. Do not merge half-finished phases into `main`. |
| **Every screen stays functional** | An unstyled screen is fine mid-redesign. A broken one is not. |
| **Claims stay honest** | See [Claims that are true today](#claims-that-are-true-today). Do not let prototype copy promise features that do not exist. |

## Order, and why it is this order

```
0 tokens ──► 1 landing      2 legal
     │
     └────► 3 app shell ──► 4 auth
                  │
                  ├──► 5 dashboard + upcoming
                  ├──► 6 meeting detail
                  └──► 7 Ask AI
                              │
                              └──► 8 polish + QA
```

- **Phase 0 first, always.** Every later phase reads those tokens. Skip it and each phase invents its own colours, which is how the current patchwork happened.
- **Phase 3 before 5, 6 and 7.** They all render inside the shell.
- 1 and 2 are independent of the app phases and can be done in any gap.

## Phase 0 — Design tokens and logo

**Goal:** the whole app shifts to the new palette in one commit. Nothing is redesigned yet.

**Files:** [frontend/src/index.css](../frontend/src/index.css), `frontend/src/assets/`

**Do**

- Replace the `@theme` block and the `.dark` block with the token set from `prototype/app/app.css` (`:root` for light, `.dark` for dark). Keep the existing token *names* wherever one already exists, so components that use `bg-surface`, `text-body`, `border-line` keep working untouched.
- Add the tokens the prototype introduces and the app has no equivalent for: `--tint`, `--tint-2`, `--tint-3`, `--accent-soft`, `--accent-line`, `--accent-ink`, the shadow scale, and the status pairs.
- Keep `@custom-variant dark (&:where(.dark, .dark *))` exactly as it is. The prototype already uses the same `dark` class and the same `theme` storage key, so nothing about theme switching changes.
- Replace the logo with the simplified mark from the prototype. Export a favicon set and an app icon from it.

**Watch for**

- The mark must be drawn with CSS classes for `fill` and `stop-color`, not presentation attributes — browsers do not resolve `var()` inside SVG presentation attributes.
- If the mark is rendered more than once on a page, each copy needs a unique gradient id, or every copy after the first loses its gradient.

**Done when:** the app runs, every page renders in the new palette, dark mode still toggles, and no component file changed.

**Status: done** — merged 2026-09-20 (`21f5058`). Nine files, all under `frontend/`.

Three things later phases inherit from it:

- **The dark primary button is illegible and phases 3 and 4 must fix it.** `--color-brand-blue` is now a light azure in dark mode (`#4DA3FF`), and every primary button is `from-brand-blue to-brand-blue-light text-white`, so white-on-azure measures 2.63 where it used to be 7.52. The prototype avoids this by flipping the dark button to a white face with dark ink (`--btn-face`, `--btn-ink`); port that rather than changing the token back, which is correct at 6.9–7.5:1 for links, nav and the accent trio.
- **`@theme` is declared `static`.** Otherwise Tailwind tree-shakes unused tokens out of `:root` while the `.dark` block keeps them, and a raw `var(--color-tint)` resolves in one theme only. Leave it.
- **Not done: the favicon set and app icon.** `public/` still carries the old PNG mark, so the browser tab disagrees with the in-app logo. Pick it up in phase 8.

Also carried forward, and not phase 0's to fix: `--color-faint` fails AA in both themes (2.79 light, 3.62 dark), and the rgba tokens must never take a Tailwind slash-opacity modifier — `bg-tint-2/50` halves an already-transparent colour.

## Phase 1 — Landing page

**Goal:** [frontend/src/pages/Landing.jsx](../frontend/src/pages/Landing.jsx) rebuilt from `prototype/index.html`.

**Do**

- Sections in order: nav, hero with the product mock, works-with strip, how it works, features bento, privacy, pricing, FAQ, closing CTA, footer.
- Port the hero mock as JSX. It replaces the striped "product shot — meeting detail" placeholder, which is the single worst thing on the current page.
- Copy: headline "Turn every meeting into knowledge.", the shortened subheadline, no version badge, no "decisions" claim, no "MOST POPULAR" badge.
- Scroll reveals via `IntersectionObserver` in a small hook. Guard the hidden state so the page is readable without JS.
- "See how it works" scrolls to the section. Do not give it a play icon unless a real video exists.

**Pricing — decided 2026-09-20: free while in beta.** One section, no tier cards, no "MOST POPULAR" badge, no price. It says the product is free during the beta and that paid plans come later, and its only call to action is the same sign-up the rest of the page uses. This is the honest option while billing does not exist, and it removes the three dead buttons the tier proposal would have shipped. Revisit when Razorpay lands.

**Done when:** it matches the prototype at 1440/820/375, reveals work, reduced-motion is respected, and nothing in the copy is untrue.

## Phase 2 — Legal pages and real footer links

**Goal:** the footer links go somewhere. Required before any public launch, because the product records other people's voices.

**Do**

- Add routes and pages for Privacy policy, Terms, What we store, Data deletion. One shared layout component; they are prose pages.
- Wire the footer. Remove any link that has no page — a dead link is worse than no link.
- Content is yours to write. Placeholder text is acceptable while drafting, but not at launch.

**Done when:** every footer link resolves, and the pages render in both themes.

## Phase 3 — App shell (the big one)

**Goal:** [frontend/src/components/Layout.jsx](../frontend/src/components/Layout.jsx) rebuilt. Every app screen sits inside this, so it lands before 5, 6 and 7.

**Do**

- **Menu sections:** Workspace (Meetings, Upcoming) · Intelligence (Ask AI) · Account (Settings). Define them in one array, as `NAV` in `prototype/app/app.js` does.
- **Three breakpoints, not one.** Desktop ≥1024: full sidebar, collapsible to a rail. Tablet 720–1023: icon rail. Phone <720: top bar plus bottom nav. The current single `lg:` breakpoint is why tablets get a stretched phone layout.
- Active item: a quiet background wash plus a 3px edge marker. Not a filled blue block.
- Sidebar footer: **one** account button opening a menu (email, Settings, theme, Sign out). Not three stacked rows, and never a truncated email as the identity line.
- Add the ⌘K command palette. **Leave the sidebar plan card out.** The prototype's "Free plan · 3 / 5 · Upgrade to Pro" has no API behind it, and with pricing now "free while in beta" there is no Pro to upgrade to — it would be two untruths in one card. Revisit alongside billing and usage metering.
- Persist the rail preference. **Lift the sidebar state out of `Layout`** — it currently lives in `useState` inside a component each page mounts separately, so collapsing it resets on every navigation.

**Watch for — both of these were found and fixed in the prototype**

- In the rail, do not hide the control that expands it again, or the user is stuck.
- The phone rule that zeroes the content margin must out-rank `body.rail .main`, or a rail preference saved on desktop leaves a phantom gap on a phone where the sidebar is not even shown.
- `overflow-y: auto` also makes the X axis scrollable. Set `overflow-x: hidden` on the nav scroller, and give rail items `title` plus `aria-label` rather than an absolutely positioned CSS tooltip.

**Done when:** every existing page still renders inside it, navigation works at all three breakpoints, the rail survives navigation, and no page has a horizontal scrollbar.

## Phase 4 — Auth pages

**Goal:** [Login.jsx](../frontend/src/pages/Login.jsx) and [Register.jsx](../frontend/src/pages/Register.jsx) from `prototype/app/login.html` and `register.html`.

**Do**

- Split layout: form on the left, brand panel on the right, panel hidden below 860px.
- Keep the existing Supabase calls exactly as they are.
- Show/hide password toggle. Inputs at 16px.
- Do not add a Google sign-in button. That flow does not exist.

**Done when:** sign in, sign up and the redirects behave exactly as before.

## Phase 5 — Meetings list and Upcoming

**Goal:** [Dashboard.jsx](../frontend/src/pages/Dashboard.jsx), [MeetingRow.jsx](../frontend/src/components/dashboard/MeetingRow.jsx), [EmptyState.jsx](../frontend/src/components/dashboard/EmptyState.jsx), [Upcoming.jsx](../frontend/src/pages/Upcoming.jsx).

**Do**

- Flat rows with dividers. No card-per-row.
- The title gets the width and stays on one line; metadata is muted and secondary.
- A line of summary text under each title. **If the list endpoint does not return a summary, leave the line out** — do not add a backend field for it in this phase. Note it as a follow-up instead.
- **A status badge only when the status is not normal.** Eight identical "Completed" badges carry no information.
- Row actions revealed on hover, always visible on touch, destructive ones behind the overflow menu.
- Filter chips scroll sideways instead of clipping at the edge.
- Upcoming: day groups with a switch per event, which is the "off by default" story made visible.
- Long titles need `break-words`; a title can be a URL with no spaces.

**Done when:** polling, retry, delete, filters, search and calendar opt-in all behave as before.

## Phase 6 — Meeting detail (the other big one)

**Goal:** [MeetingView.jsx](../frontend/src/pages/MeetingView.jsx), [MeetingDetails.jsx](../frontend/src/components/MeetingDetails.jsx), [AudioPlayer.jsx](../frontend/src/components/meeting/AudioPlayer.jsx), the three tab components, [MobileChatSheet.jsx](../frontend/src/components/meeting/MobileChatSheet.jsx).

This is the screen with the real layout problem. On a 375px viewport the prototype gives content 65% of the height; today it gets roughly 40px.

**Do**

- **One sticky header line:** back, title on one line, metadata inline beside it, actions on the right. Delete moves into the overflow menu.
- **Audio becomes a slim bar pinned to the bottom of the column.** Give the scrubber ~22px of hit area around a 4px track; the current 4px line is close to untappable.
- **One scroll container.** Remove the inner `overflow-y-auto` and the fixed `h-[calc(100vh-4rem)]` card. No card inside a card, no nested scrollbar.
- Chat column collapsible on desktop, with the state remembered. On phones it opens as a sheet from a button instead of a permanently docked panel.
- Action items: the text gets the room. The owner pill and due date must not squeeze it into a one-word column.
- Fixes the 1024–1200px squeeze by itself, because the fixed 420px chat column becomes collapsible.

**Done when:** polling, stop, retry, PDF export and the chat all work, and at 1024px nothing in the header is clipped.

## Phase 7 — Ask AI

**Goal:** [AskAI.jsx](../frontend/src/pages/AskAI.jsx), [ConversationList.jsx](../frontend/src/components/ask/ConversationList.jsx), [ChatInterface.jsx](../frontend/src/components/ChatInterface.jsx).

**Do**

- Full-bleed on phones. Remove the bordered card with its own scrollbar, and the `h-[calc(100dvh-13rem)]` cap that clips the starter prompts mid-row.
- Desktop: thread column plus chat. Phone: the same list as a drawer.
- Starter prompts wrap and scroll with the page.
- **Do not touch `useAskAiChat` or `useAskAiConversations`.** Streaming, trimming and infinite scroll are working code.

**Done when:** streaming, citations, rename, delete, delete-all and infinite scroll all behave as before.

## Phase 8 — Polish and QA

**Do**

- Replace every `window.alert()` with toasts — Dashboard, MeetingView, Upcoming and AskAI all use them today, and nothing reads as unfinished faster.
- Skeletons in place of bare spinners on first load.
- Every input at **16px**, or iOS Safari zooms the page on focus. Today the dashboard search, both chat inputs, the settings textarea and the landing email field are all below that.
- Touch targets ≥44px.
- `prefers-reduced-motion` honoured everywhere.
- Long unbroken titles: `break-words` on the mobile row, the meeting heading and chat answers.
- A max width on the list pages so they do not stretch on a 1920px monitor.
- `scroll-margin-top` on landing anchor targets, so sections do not land under the sticky header.
- Consider `viewport-fit=cover` in `index.html`; without it the bottom nav's `env(safe-area-inset-bottom)` resolves to 0.

**Full sweep:** every screen at 320, 375, 820, 1440 in both themes. No horizontal overflow, no console errors.

## Verification for every phase

1. `npm run lint` and `npm run build` both clean.
2. Screenshots at 320, 375, 820 and 1440, in both themes.
3. `document.documentElement.scrollWidth` must equal the viewport width at every size. Horizontal overflow is a bug, not a detail.
4. Click the real flows the phase touched — not just "it renders".
5. Confirm `git diff --stat` shows **no** files under `meeting-bot/`, and none under `backend/` unless that phase documented why.

A scripted pass with Playwright catches items 2–3 in a few seconds per phase; `meeting-bot/node_modules` already has Chromium available for it.

## Claims that are true today

Copy anywhere in the app or landing page must stay inside this list.

| Claim | Status |
|---|---|
| Google Meet and Zoom | True |
| Audio only, no video stored | True |
| 90-minute recording cap | True (`MAX_RECORDING_DURATION_MINUTES`) |
| Google Calendar sync, off until an event is opted in | True |
| PDF export | True — **Markdown export does not exist** |
| Ask AI across meetings, with citations | True |
| Delete removes audio, transcript and chat | True |
| Dashboard search | **Titles only** |
| Billing, trials, SSO, admin controls, retention policies | **Do not exist** |
| "Free while in beta" | True — and the only pricing claim the app may make |
| "Decisions" extracted from a meeting | **Does not exist** — summary, key points, conclusion, action items |

## Schedule (revised 2026-09-20)

The earlier two-day cut — phases 0 → 3 → 5, then 6, with the landing page demoed from the prototype HTML — is superseded. The whole frontend is being built on 2026-09-20, ahead of the manager demo on 2026-09-22.

Phase 0 is merged. The remaining order, which keeps every screen demoable at each stop:

1. **3 — app shell.** Everything else renders inside it, so it goes first.
2. **5 — meetings list and upcoming**, then **6 — meeting detail**. The two screens the demo actually walks through.
3. **7 — Ask AI**, then **4 — auth**. Auth is small and self-contained; it can slip without hurting the demo.
4. **1 — landing**, then **2 — legal pages**. Independent of the app phases.
5. **8 — polish and QA** across whatever landed.

If the day runs out, stop at a phase boundary and leave the rest on the old design inside the new shell. A half-finished phase is the one outcome to avoid — an unstyled screen is fine, a broken one is not.

## Not included

- **Billing and Razorpay.** Deferred until after the frontend. Test keys are already in `backend/.env`, with placeholders in `backend/.env.example`.
- **Usage metering.** Counting recording hours, enforcing caps mid-call. Backend work, and the harder half of billing.
- **Pricing tiers.** Dropped from the landing page entirely (see phase 1); it says "free while in beta" instead. Tiers return with billing, not before.
