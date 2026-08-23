# Pre-Delivery Audit

Date: 2026-08-24
Scope: full repo (`backend/`, `frontend/`, `meeting-bot/`, `supabase/`), including the currently uncommitted/staged changes shown in `git status`.

This is a report only. Nothing in this document has been fixed yet.

## How to read this

Findings are grouped by the six areas requested, then rolled up into one ranked punch list at the end. Severity levels:

- **Ship-blocker** — must be fixed before this goes live; either breaks a core flow, leaks data, or leaves the system in a bad state with no recovery.
- **Should-fix** — real bug or gap, not launch-day critical, but will bite users or the team soon after.
- **Cleanup-only** — correctness is fine; this is hygiene, consistency, or polish.

---

## 1. Dead code and unnecessary files

### Tracked files that shouldn't ship
- `meeting-bot/zoom-no-input.png`, `zoom-step1.png`, `zoom-step2.png`, `zoom-step2-direct.png`, `zoom-step2-no-link.png`, `zoom-step3.png`, `zoom-step4.png` are **still tracked in git** even though the root `.gitignore` now lists them. Adding a file to `.gitignore` doesn't untrack it — these need `git rm --cached`. **Should-fix.**
- `backend/test.db` still exists on disk (16KB) even though it's staged for deletion and now gitignored. No code references it by filename (`database.py` requires `DATABASE_URL` from env, fails loudly if unset). Harmless, just delete it locally. **Cleanup-only.**
- `frontend/design/MeetIQ Screens.html` (~483KB reference export) is correctly covered by `frontend/.gitignore` (`design/`) and excluded from the Docker build context — confirmed not an issue.
- `backend/static/images/MeetIQ_logo.png.png` → `MeetIQ_logo.png` rename is clean; zero remaining references to the old double-extension name anywhere in the tree.
- `meeting-bot/generate-auth.cjs` / `generate-zoom-auth.cjs` are local-only interactive auth tools, correctly dockerignored, and only referenced from an error message telling a developer what to run — not dead weight in any harmful sense.

### Runtime debug screenshots — the biggest hygiene finding
`meeting-bot/src/platforms/zoom/ZoomBot.js` (lines 93, 110, 114, 154, 162, 174, 187) and `meeting-bot/src/platforms/google-meet/GoogleMeetBot.js` (lines 51, 62, 99, 108, 133) unconditionally call `page.screenshot(...)` with hardcoded filenames on **every real join**, not behind a debug flag. This overwrites `zoom-step1.png` … `google-meet-prejoin.png` etc. on the live container's disk on every meeting. These are screenshots of a real user's live meeting window, persisted unencrypted with no cleanup or access control. Root-level `google-meet-*.png` files on disk (outside `meeting-bot/`) confirm this is live, reproducible behavior from local runs, not a one-off. **Should-fix, arguably ship-blocker on privacy grounds** — recommend gating behind a `DEBUG_SCREENSHOTS` env var before shipping, and confirm the .gitignore covers all these filenames (see below).
- `google-meet-auth-expired.png` and `google-meet-anonymous-session.png` are covered by `.dockerignore`'s blanket `*.png` rule but **not** by the root `.gitignore` (which only whitelists specific filenames, no blanket pattern) — a stray `git add .` from local debugging could commit real meeting screenshots. **Should-fix**: add a blanket `*.png` exception pattern to the root `.gitignore` for these directories, matching what `.dockerignore` already does.

### Dockerfile inconsistencies
- **`backend/Dockerfile` (production) never installs `ffmpeg`** — only `Dockerfile.dev` does. See §3/§5 below; this silently breaks the silence-detection feature in production. **Ship-blocker.**
- `backend/Dockerfile` uses `python:3.12-slim`, `Dockerfile.dev` uses `python:3.11-slim` — version drift between dev and prod. **Should-fix.**
- `frontend/Dockerfile` uses `node:20-bookworm-slim`, `Dockerfile.dev` uses `node:22-alpine` — same kind of drift. **Should-fix.**
- `meeting-bot/` has only one `Dockerfile` (no `.dev` variant) by design — `docker-compose.yml` builds the same production Dockerfile for local dev and bind-mounts `auth.json`/`zoom-auth.json` instead. Not an inconsistency.

### Commented-out code / debug logging
- `backend/app/services/pdf_service.py:114-211` — ~100 lines of fully commented-out duplicate `MeetingPDF`/`generate_meeting_pdf` code from an earlier draft (Helvetica vs DejaVu fonts). Delete before ship. **Cleanup-only.**
- `meeting-bot/src/platforms/zoom/ZoomBot.js` / `GoogleMeetBot.js` have extremely verbose per-frame/per-input `console.log` diagnostic dumps — same debug-scaffolding origin as the screenshots. Confirmed via grep: none of it prints a token, bearer header, cookie, or API key. **Cleanup-only**, bundle with the screenshot cleanup.
- Backend mixes the `logging` module with raw `print()` for operational messages (`meetings.py`, all of `transcription_service.py`/`embedding_service.py`/`cost_tracker.py`). The `[cost]`/`[transcription]` prefixes are an intentional convention, not debug leftovers, but bypass log levels/timestamps. **Cleanup-only.**

### Unused imports (recently modified files)
- `backend/app/api/meetings.py:1` — `BackgroundTasks` imported, never used.
- `backend/app/rag/chat_service.py:1` — `import inspect`, never used.
- `backend/app/api/meetings.py:101` — `from sqlalchemy import update` is a mid-file import, style-only.
- Everything else touched in this diff (frontend `.jsx`, meeting-bot `.js`, `config.py`, `main.py`, `bot_service.py`, `cost_tracker.py`) has clean, fully-used imports. **Cleanup-only.**

### Orphaned files
None found. Every component under `frontend/src/components/` and `frontend/src/pages/`, every module under `backend/app/`, and every file under `meeting-bot/src/` is reachable from an entry point.

---

## 2. Consistency between layers

### Migrations
- **The `meetings` table is never `CREATE TABLE`'d anywhere in the alembic chain — only ever `ALTER`'d.** Only `475e9ee29ad9_initial_schema.py` (creates `users`) and `c3d9f2a1b4e7_create_meeting_chunks_table.py` (creates `meeting_chunks`) contain a `CREATE TABLE`. `alembic upgrade head` against a genuinely empty database fails immediately (`475e9ee29ad9` truncates `meeting_chunks` before it exists, and the `meetings` ALTERs have nothing to alter). The chain only works as an incremental diff against the one already-existing production database — it cannot bootstrap a new environment (fresh staging/dev DB, disaster recovery). **Ship-blocker** — add a `CREATE TABLE IF NOT EXISTS meetings (...)` migration analogous to the `meeting_chunks` one.
- Aside from that gap, the chain is confirmed linear with a single head: `475e9ee29ad9` → `8f2c1a9d4b6e` → `c3d9f2a1b4e7` → `e6a4d8f0b2c1` → `f7b1c9d3e5a2` → `a1b2c3d4e5f6` → `b2c3d4e5f6a7`. No branches, no orphaned revisions.
- Every model field has a migration and every migration column has a model field — checked across `User` (`id`, `email`, `bot_display_name`, `created_at`) and `MeetingChunk`. No mismatches.
- `supabase/migrations/create_meetings.sql` is a **stale, pre-alembic leftover** — it defines `meetings` with only 9 columns, missing `user_id`, `title`, `embedding_provider`, `updated_at` (all added later via alembic). Nothing in the backend executes it at runtime; it's not wired to anything. Actively misleading for a future contributor who might re-run it against a fresh Supabase project and get a schema three migrations behind. **Should-fix**: delete it or mark it clearly historical.
- Naming footgun: `backend/app/models/meeting.py` is **not** the SQLAlchemy model — it's Pydantic request/response schemas. The real ORM models live in `backend/app/db/models.py`. Worth a rename or a comment for future contributors. **Cleanup-only.**

### Column usage
- All columns in `meetings`, `meeting_chunks`, and `users` are read or written somewhere. `embedding_provider` and `updated_at` are internal-only (never serialized to frontend) but actively used, not dead.
- `users.created_at` is written but never read back anywhere. **Cleanup-only.**
- `meeting_to_dict()` (`backend/app/api/meetings.py`) returns `user_id` in every response, but no frontend code ever reads `meeting.user_id`. Not a bug, just an unused response field. **Cleanup-only.**

### API ↔ frontend
Enumerated every route in `backend/app/api/{meetings,chat,users,webhooks}.py` against every call in the frontend. All endpoints are called correctly (method, path, params), and there are no frontend calls to nonexistent routes and no dead backend endpoints. Every field the frontend reads is actually present in the response shape, including the conditional `audio_playback_url`.

Error-handling gaps found:
- `Dashboard.jsx` (`fetchMeetings`) — on any fetch failure (network error, expired session, 500), only `console.error`s and leaves `meetings=[]`. The UI then renders the "no meetings yet" empty state, indistinguishable from a genuinely empty account. A user whose session expired sees no error and may think their recordings were deleted. **Should-fix.**
- `MeetingView.jsx` does show a visible error on fetch failure, but the same generic message covers 404, 401, and 5xx — no differentiation. **Cleanup-only.**
- Delete/retry handlers in `Dashboard.jsx`/`MeetingView.jsx` sometimes surface the backend's specific error detail (e.g. the 409 "Only failed meetings can be retried" message) and sometimes just show a generic `alert("Failed to delete meeting.")`, discarding a more useful backend message. **Cleanup-only.**

### meeting-bot ↔ backend contract
- `POST {meeting_bot_url}/{platform}/join` and `POST /stop` payload shapes match exactly on both sides, including the shared bearer token and field names.
- **`teams` is wired through the allowlist and API routing but has no actual bot implementation.** `platform_detector.py` allows `teams.microsoft.com`/`teams.live.com`; `bot_service.py` maps a `/teams/join` endpoint; `meeting-bot/src/api/server.js` registers the route — but `MeetingLifecycle.js`'s `BOT_CLASSES` only maps `google` and `zoom`. A submitted Teams URL gets accepted (202), then the lifecycle throws `Unsupported platform: teams` *before* entering the try/finally block, so the webhook that would report failure never fires. The meeting is left stuck in `status="joining"` until the watchdog's 10-minute TTL sweep cleans it up. The frontend doesn't offer Teams as an option, but nothing stops a user from pasting a Teams link. **Ship-blocker** (silent stuck state on a supposedly-supported platform) — either implement a Teams bot or reject `teams` until one exists.
- The reverse direction (meeting-bot → backend webhook) matches cleanly: both the progress-ping and final-report payload shapes match the backend's `RecordingCompleteWebhook` model, and the backend's `status NOT IN (...)` guards correctly prevent progress pings from racing the final report.

### RLS
- All three user-data tables (`users`, `meetings`, `meeting_chunks`) have RLS enabled with correct ownership-scoped policies (`user_id = auth.uid()` for `meetings`/`users`, a join-based `EXISTS` check for `meeting_chunks`). This is explicitly documented as defense-in-depth since the backend's actual DB connection is a service-role/DB-owner connection that bypasses RLS by design — application-layer ownership checks (below) are what actually gate end-user requests, and those are all present. No drift, no later migration weakens this.

---

## 3. Regressions from recent work

| Feature | Status | Key gap |
|---|---|---|
| Meeting titles | Complete | No UI to manually edit a title — it's auto-generated/read-only. Not a regression, just a possible scope gap if editability was expected. |
| Delete endpoint | **Incomplete — ship-blocker** | See below. |
| Structured action items | Complete (pre-existing, not part of this diff) | None found. |
| Bot display name | Complete, fully wired | None found. |
| Waiting-for-admission + 5-min timeout | Complete, well-hardened | None found — timeout, webhook, and watchdog backstop all correctly connected. |
| Cost logging | **Incomplete — should-fix** | Write-only; no persistence, no read path. |
| Watchdog | Complete (pre-existing) | Only watches DB status TTLs, not the bot process directly — consistent with the design, not a gap. |
| Frontend redesign | Mostly complete | Landing page ships placeholder content and unimplemented pricing (see below). |

Details:

- **Delete endpoint doesn't stop an in-progress bot.** Ownership check and DB/storage cascade are all correct, but `delete_meeting` never checks `meeting.status` and never calls `stop_bot()` — unlike the sibling `/stop` endpoint, which does. Deleting a meeting that's currently recording removes the DB row immediately, but meeting-bot keeps recording for up to 90 minutes; when it finally finishes and calls the webhook, the meeting is gone (404), the bot gives up after 3 retries, and the uploaded recording is permanently orphaned in storage. The user believes they deleted the meeting; the bot records it anyway. **Ship-blocker.**
- **Cost tracking is print-only.** `cost_tracker.py` logs `[cost] ...` lines to stdout from the chat, embedding, and transcription services, but nothing persists it to a DB table/column and there's no API endpoint or frontend page to view it. Lost on every restart/log rotation. If any user- or admin-facing cost visibility was expected, that half doesn't exist yet. **Should-fix.**
- **Landing page ships unimplemented product claims.** `frontend/src/pages/Landing.jsx` advertises concrete paid tiers ("$8/month Pro", "$6/user/month Team", "5 meetings/month free", 30-min/4-hour recording caps) — none of this is backed by any Stripe/billing integration, plan field, or quota enforcement anywhere in the repo. The only recording cap that exists is a single global `MAX_RECORDING_DURATION_MINUTES=90` env var, unrelated to any tier. This is fine as a mockup but a real risk if it goes live as-is and someone tries to sign up expecting billing to work. **Should-fix** (product-level, not a code bug).
- Landing page also has a literal placeholder box reading "product shot — meeting detail" instead of a real screenshot, and several footer nav items ("How it works", "Pricing", "Changelog", etc.) render as plain non-clickable `<div>`s. **Cleanup-only.**

---

## 4. Security re-check

All five previously-fixed issues were re-verified and are **still in place, not reverted**:

1. **URL host allowlist** (`platform_detector.py`) — still correct: exact-host-or-subdomain match with a required leading dot, `https`-only, and it's the single entry point for user-submitted URLs (`POST /meetings`). Google's bot also re-checks the hostname immediately before navigating as defense-in-depth.
2. **Bearer-token comparison** — still constant-time on both sides (`crypto.timingSafeEqual` in `meeting-bot/src/api/server.js`, `hmac.compare_digest` in `backend/app/api/auth.py`), including the zero-length-buffer edge case.
3. **UUID validation in the join handler** — still present and correct in `meeting-bot/src/api/server.js` (`meetingId`/`userId` regex-checked before use in file paths). New gap found on the backend side (see below).
4. **Ownership checks on every endpoint** — every resource-id endpoint in `meetings.py`, `chat.py`, and the RAG tools scopes its query by `user_id`. No endpoint returns or modifies another user's data.
5. **RLS migrations** — enabled and correctly scoped for all three user-data tables; the documented service-role bypass is intentional, not a regression.

New findings from the current diff / general sweep:

- **Backend `meeting_id` path params are never UUID-validated.** Unlike the meeting-bot's own `UUID_RE` check, every `{meeting_id}` route in `meetings.py`/`chat.py` passes the raw string straight into a UUID-typed column filter. A malformed id (`GET /meetings/not-a-uuid`) raises an unhandled Postgres `InvalidTextRepresentation` error — there's no global FastAPI exception handler in `main.py` to convert it to a clean 400/404, so it surfaces as an unhandled 500. Not an injection risk (parameterized), but poor API hygiene and a possible info leak if debug mode is ever on. **Should-fix.**
- **ZoomBot lacks the defense-in-depth host re-check that GoogleMeetBot has.** `ZoomBot.buildDirectWebClientUrl` hardcodes `app.zoom.us` as the navigation target in the normal case, but if `new URL(inviteUrl)` throws, it silently falls back to navigating to the raw, unvalidated `inviteUrl`. Reaching this requires a URL that passes both Pydantic's `HttpUrl` check and the backend's host allowlist yet still fails Node's `URL` parser — unlikely, not impossible. **Should-fix**, add the same explicit hostname assertion Google's bot has.
- **Historical `.dockerignore` bug, now fixed in the current diff, needs verification.** A prior version of `meeting-bot/.dockerignore` used a `meeting-bot/`-prefixed path inside a build context that's already `meeting-bot/`, so the auth-file exclusion never matched — every previous Docker build silently baked the real Google/Zoom session cookies into the image layer. The current diff fixes the path. **Ship-blocker if any such image was ever pushed to a registry or shared** — treat any previously-distributed image as compromised and rebuild; otherwise this is resolved, just verify with `docker history`/layer inspection before assuming it's clean.
- No new SQL built via string concatenation, no new endpoints missing auth, no secrets logged (grepped explicitly across all touched files — zero matches), no new `eval`/`exec`/`child_process.exec` with untrusted input, and CORS in `main.py` remains origin-scoped (not `*`).

---

## 5. Bugs and edge cases

- **Meeting lifecycle / stuck-state handling is solid.** Every transition (`joining → waiting_for_admission → recording → uploading → transcribing → completed/failed`) is wrapped so that any thrown error reaches a terminal `failed` state, backed by webhook retries with backoff plus a backend watchdog TTL sweep per non-terminal status. This area has clearly already been hardened by a prior audit and is not regressed by the current diff. The one exception is the delete-during-recording gap already called out in §3.
- **No exploitable race conditions found.** Stop-vs-webhook and delete-vs-webhook races are both handled cleanly (idempotent stop, `rowcount == 0` guards preventing orphaned transcription jobs against a deleted row). Chat sessions are serialized per-conversation with an `asyncio.Lock`.
- **`Dashboard.jsx` swallows fetch failures into a blank "no meetings" state** — already noted in §2, repeated here because it's the clearest silent-failure case in the codebase. **Should-fix.**
- **`db_user.bot_display_name` is dereferenced without a null check** in `meetings.py` — currently safe only because `get_current_user` always creates the user row first; a latent landmine if that invariant is ever broken by a future refactor. **Cleanup-only.**
- No inappropriate silent failures elsewhere: all bare `except`/`catch` sites reviewed are either intentionally best-effort (screenshot cleanup, cookie refresh) or followed by explicit failure propagation.
- **`ffmpeg`/`ffprobe` calls in `transcription_service.py` fail silently in production** because the production Dockerfile doesn't install `ffmpeg` (see §1/§3). Both call sites wrap the subprocess call in a bare `except Exception` that swallows the resulting `FileNotFoundError`, so silence-detection just silently never fires and cost logs show `audio_sec: "unknown"` — no error surfaced anywhere. Since `docker-compose.yml` only builds `Dockerfile.dev` locally, this would never be caught before a real production deploy. **Ship-blocker.**

---

## 6. Config and secrets

- No real secrets, credentials, keys, or PEM files are tracked in git (`git ls-files` grep for `.env`/`secret`/`credential`/`.pem`/`.key` returns only the three `.env.example` files).
- `auth.json`/`zoom-auth.json` are correctly untracked and gitignored everywhere they need to be.
- All three `.dockerignore` files and the root `.gitignore` are largely self-consistent; the two gaps are the tracked `zoom-*.png` files (§1) and the missing blanket png pattern in the root `.gitignore` for the runtime debug screenshots (§1).
- **Two env vars are read but undocumented**: `meeting-bot/src/core/BrowserManager.js` reads `process.env[cfg.envVar]` dynamically for `AUTH_STATE_PATH` and `ZOOM_AUTH_STATE_PATH`, neither of which appears in `meeting-bot/.env.example`. **Should-fix.**
- **Zoom's auth file is never validated at startup.** `assertAuthStateExists()` is called with no `platform` argument, defaulting to `'google'` only — despite an explicit comment stating the intent is to "fail loudly at startup, not on the first meeting join." A broken `ZOOM_AUTH_STATE_PATH` only surfaces when a Zoom meeting is actually joined, contradicting that stated intent. **Should-fix.**
- All other env vars are documented and fail loudly: backend required vars have no defaults in the pydantic `Settings` class (or raise `ValueError` explicitly, e.g. `DATABASE_URL`), and meeting-bot checks `REQUIRED_ENV_VARS` at startup with `process.exit(1)`.
- `backend/app/db/database.py` reads `DATABASE_URL` via a manual `os.environ.get()` + `load_dotenv()`, entirely bypassing the pydantic `Settings` class in `config.py` (which has no `database_url` field at all) — two separate, inconsistent config-loading mechanisms in the same app. Not a bug today, but a maintenance trap. **Cleanup-only.**
- Redundant/dead entries in `meeting-bot/.dockerignore` (named `.exe` helper files that don't exist on disk, screenshot filenames listed individually underneath a blanket rule that already excludes them). **Cleanup-only.**

---

## Ranked punch list

### Ship-blockers (fix before delivery)
1. **Alembic chain never creates the `meetings` table** — a fresh database can't be bootstrapped from migrations alone.
2. **Delete endpoint doesn't stop an in-progress bot** — deleting a recording meeting orphans the recording and leaves a ghost session running for up to 90 minutes.
3. **Production Dockerfile is missing `ffmpeg`** — silently disables silence-detection and duration logging in production; failure is fully swallowed.
4. **Teams platform is reachable but has no bot implementation** — any submitted Teams URL leaves a meeting stuck in `joining` for ~10 minutes with no error surfaced to the user.
5. **Verify no Docker image with real session cookies baked in was ever pushed/shared** — a real historical leak, now fixed in the uncommitted diff, but needs a rebuild-and-verify pass (or treating any distributed image as compromised).
6. **Unconditional debug screenshots of live meetings written to the container filesystem in production** — a privacy issue, not just leftover debug code.

### Should-fix (soon after delivery)
- `supabase/migrations/create_meetings.sql` is a stale, misleading pre-alembic leftover — delete or clearly mark historical.
- Backend `meeting_id` path params aren't UUID-validated → unhandled 500 on malformed input.
- ZoomBot missing the same defense-in-depth host re-check GoogleMeetBot has.
- `Dashboard.jsx` shows a blank "no meetings" state instead of surfacing fetch errors.
- Cost tracking is print-only — no persistence or read path if visibility was ever intended.
- Zoom auth file isn't validated at startup (only Google's default path is).
- `AUTH_STATE_PATH`/`ZOOM_AUTH_STATE_PATH` env vars undocumented in `.env.example`.
- Dev/prod Dockerfile version drift (Python 3.11 vs 3.12, Node 20 vs 22).
- `zoom-*.png` screenshots still tracked in git despite being gitignored — needs `git rm --cached`.
- Root `.gitignore` missing a blanket pattern for runtime debug screenshots (only exact filenames listed).
- Landing page advertises paid tiers/quotas with zero billing implementation behind them — product risk if shipped as-is.

### Cleanup-only (no rush)
- ~100 lines of commented-out dead code in `pdf_service.py`.
- Unused imports (`BackgroundTasks` in `meetings.py`, `import inspect` in `chat_service.py`).
- `backend/test.db` stray file still on disk locally.
- `users.created_at` and API-returned `meeting.user_id` are unused.
- `db_user.bot_display_name` dereferenced without a null check (currently safe only by invariant).
- No UI to manually edit a meeting title (auto-generated only — may be intentional).
- Landing page placeholder hero image and non-clickable footer links.
- Naming footgun: `backend/app/models/meeting.py` holds Pydantic schemas, not the SQLAlchemy models (those are in `backend/app/db/models.py`).
- `database.py` bypasses the pydantic `Settings` class with its own manual env loading — duplicate config mechanisms.
- Logging style inconsistency (`print()` vs the `logging` module) and verbose diagnostic `console.log` dumps in the Zoom/Google Meet bots.
- Dead/redundant entries in `meeting-bot/.dockerignore`.
