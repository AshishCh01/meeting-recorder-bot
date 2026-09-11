# Full-Codebase Audit — 2026-09-11

Scope: entire repo (`backend/`, `frontend/`, `meeting-bot/`, `supabase/`) at commit
`d424ea9` (main, 2026-09-11). Static analysis only — nothing was run, built, or
executed. This report (1) verifies which findings from the nine prior audit docs
in this folder are actually fixed on current `main`, (2) checks progress against
`docs/scaling-plan.md`, and (3) adds a fresh bug-hunting pass over code those
audits didn't cover in depth. Three independent review passes were cross-checked
against each other and, where flagged as uncertain, against the actual source.

**Bottom line: not production-ready yet, but closer than the raw finding-count
suggests.** The correctness core (auth, RLS, scheduler race, transcription
durability, DB pool exhaustion) is genuinely solid and has been proven with
falsified tests, not just code review. What's missing is mostly in two places:
(1) the meeting-bot has no graceful-shutdown path, so deploying a new bot
version while someone is mid-recording destroys that recording outright, and
(2) there's no rate limiting, no CI, and no operator-facing recovery path for
one specific failure mode (indexing succeeds-transcript-but-RAG-fails). See
[§4 Deploy readiness](#4-deploy-readiness-verdict) for the full reasoning.

---

## 1. Scaling-plan.md progress

| Phase | Item | Status |
|---|---|---|
| A1 | Scheduler claims rows atomically | ✅ Done, tested, falsified |
| A2 | Confine sweep loops to one replica | ⛔ **Not done** — blocked on infra (single EC2 instance, no load balancer yet), zero code required when it happens |
| A3 | Transcription moves to a queue (arq + Redis) | ✅ Done, tested, falsified, verified live incl. Redis-restart durability |
| A4 | Sentry + structured logging init | ✅ Done |
| A5 | JWTs verified locally (no per-request Supabase round-trip) | ✅ Done — 53% p50 latency cut, forgery-tested against a live server |
| A5-residual | DB pool sized to fit Supabase's 15-connection cap, 503 not 500 on exhaustion | ✅ Done |
| A5-residual | One unexplained 30s stall seen once in benchmarking | ⛔ **Still open, unexplained** — not reproduced since, root cause unknown |
| A5-residual | 5 more routes hold a DB connection through a slow network call (`calendar.py` list/schedule events, `meetings.py` retry/reupload/stop/delete/create) | ⛔ **Still open** — same pattern already fixed in 3 other routes (`get_meeting`, `search_transcript`, chat), not yet applied here |
| B | Broaden test coverage (status machine, webhook idempotency, AI fallback ladders, chat reset semantics, worker retry) | ⛔ **Not done** — deferred by decision |
| B | CI pipeline | ⛔ **Not done** — no `.github/workflows`, tests only run if someone remembers |
| B | Convert 45 `print()` calls to structured `logger.*` | ⛔ **Not done** |
| B | `log_cost` → real metric (currently print-only) | ⛔ **Not done** |
| B | Rate limiting (chat, meeting creation) | ⛔ **Not done — none exists anywhere in the backend**, confirmed by grep |
| C1 | `GET /capacity` on meeting-bot | ✅ Done |
| C2 | Bot dispatch behind a queue (queued → joining, capacity-aware) | ✅ Done, 32 tests |
| C3 | Bot registry (multi-host) | ⛔ **Not started** — no `BOT_HOST_ID`/`BOT_REGISTRY_URL` anywhere in code |
| C4 | Per-host auth identity | ⛔ **Not started** |
| C5 | Remove single-session `/stop` fallback | ⛔ **Not started** |

**Count: 11 distinct items from the plan remain open.** Of those, A2 and C3–C5
are explicitly deferred-by-design (infra doesn't exist yet / single-host
deployment is the current target, so multi-host work is premature). The two
that are load-bearing for a production launch and are **not** "wait for
infra" items are: **rate limiting** (nothing stops a user looping
`/chat/stream` against paid LLM APIs) and **the 5 remaining connection-holding
routes** (same class of bug already fixed three times elsewhere, so it's a
known, mechanical fix, not a design question).

---

## 2. Bugs and loopholes — current state

### Critical

**C-1. meeting-bot has no graceful shutdown — a deploy or restart during an active recording destroys it, with no cleanup and no notification to the backend.**
Verified directly: no `process.on('SIGTERM'|'SIGINT'|...)` anywhere in
`meeting-bot/src/index.js` or `meeting-bot/src/api/server.js`. Node's default
behavior on an unhandled `SIGTERM` is immediate termination — ffmpeg never gets
the graceful stop sequence `FFmpegManager` already implements for the `/stop`
API path, so the `.m4a` is left unfinalized; Chrome and the PulseAudio sink are
never released; `notifyBackend`'s webhook never fires. The meeting sits in
`"recording"` until the watchdog's TTL (up to 105 minutes by default) finally
fails it — so the user doesn't even find out for up to an hour and 45 minutes.
This will happen on **every single deploy of the bot** while anyone is
recording, not as an edge case. Compounding factor, lower confidence: the
Docker entrypoint (`meeting-bot/docker-entrypoint.sh:29`) execs `npm start`
rather than `node` directly, and npm has a history of not reliably forwarding
signals to its child process — so even adding a handler may need the `CMD` to
invoke `node src/index.js` directly (or add `tini`) to guarantee it's reached.
**Fix priority: before launch**, or accept that every bot deploy will silently
eat whatever recording is in flight.

### High

**H-1. No rate limiting anywhere in the backend.** Confirmed by grep — zero
matches for rate-limiting logic outside test files and Sentry's own internal
event-rate-limiting. `/chat/stream` proxies to paid Gemini/Groq calls with only
a 4000-character body cap; nothing stops a loop. Already flagged in
`scaling-plan.md` Phase B as deferred-by-decision, but worth restating as a
launch risk, not just a scaling one: this is a direct cost-abuse vector, not a
performance concern.

**H-2. A meeting that finishes transcription but fails RAG indexing is permanently stuck with no recovery path.** `transcription_service.py`'s
`record_terminal_failure` deliberately leaves `status = "completed"` (so the
summary/transcript/PDF still work) when indexing exhausts its retries, setting
only `error_message`. But `POST /meetings/{id}/retry` only accepts
`status == "failed"`, and the frontend's Retry button and error banner are
both gated on that same check (`MeetingDetails.jsx:135,144-151`). Result: the
user sees a normal-looking completed meeting, asks a question in chat, and
gets "no indexed transcript chunks found" with **zero indication anything is
wrong**, let alone how to fix it — the only recovery is a manual SQL query an
operator has to know to run (`SELECT id FROM meetings WHERE status='completed'
AND embedding_provider IS NULL`). This is a real, self-inflicted dead end: the
code anticipated the failure mode but never wired a way back out of it.

**H-3. Five routes still hold a DB connection open through a slow external call**, the same bug class already fixed three times this month (`get_meeting`, `search_transcript`, both chat routes). Still open in:
- `backend/app/api/calendar.py` `list_events`/`schedule_event` — holds through a Google token refresh + a second Google API call.
- `backend/app/api/meetings.py` `retry_meeting`/`_reupload_from_bot` — holds through a 10s bot HTTP call.
- `backend/app/api/meetings.py` `stop_meeting`/`delete_meeting` — holds through `stop_bot()` (10s timeout) and, for delete, a Storage removal.
- `backend/app/api/meetings.py` `create_meeting` — holds through `trigger_bot_join` (10s bot call, or a Redis enqueue).

At the current DB pool size (8 connections for `backend`), a handful of slow
bot calls or Google API calls landing at once can exhaust the pool and start
handing out 503s to unrelated requests — the exact failure mode A5's own
benchmark surfaced and the team has already fixed elsewhere. This is
mechanical, not a design question.

### Medium

**M-1. No Docker healthchecks anywhere, despite both `backend` and `meeting-bot` exposing `/health`.** Neither compose file defines a `healthcheck:` block for `backend`, `worker`, `meeting-bot`, `redis`, or `frontend`. `depends_on` only waits for container *start*, not readiness, and `restart: unless-stopped` only restarts a container that has *exited* — a hung event loop or a wedged Xvfb/PulseAudio inside `meeting-bot` (which keeps its port open) is invisible to Docker and never auto-recovers.

**M-2. `FRONTEND_ORIGIN` silently defaults to `localhost` with no startup validation.** Every other production-critical setting (`supabase_url`, `gemini_api_key`, the bearer token) has no default and fails loudly at import if missing. `frontend_origin` does not, and it's used directly as the sole CORS `allow_origins` entry outside dev mode. Forgetting to set it on a production deploy produces a confusing browser-side CORS error with nothing in the backend logs pointing at the cause.

**M-3. AuthKeepAlive's periodic browser sessions aren't counted against bot capacity or memory sizing.** Every 15 minutes (default), `AuthKeepAlive.js` launches a full headed Chrome for Google and then Zoom, independent of `activeMeetings`/`MAX_CONCURRENT_MEETINGS` and invisible to `GET /capacity`. `shm_size` in both compose files is sized specifically for the configured number of *concurrent meeting* Chromium instances — a keepalive cycle firing mid-recording briefly runs an uncounted extra Chromium on a host sized without headroom for it.

**M-4. `bot.leave()`/`context.close()` has no timeout**, in both `ZoomBot.js` and `GoogleMeetBot.js`. A wedged Chromium/CDP connection can hang the entire shutdown path indefinitely, blocking the single-active-meeting slot from ever freeing up. `BrowserManager.js` now correctly cascades `context.close()` into `browser.close()` (the earlier leak fix), but neither call is time-bounded.

**M-5. Landing page advertises paid tiers ($8/mo Pro, $6/user/mo Team) with no billing integration anywhere in the repo.** Not a code bug, but a real go-to-market gap if launch means "accept payment" — there is no Stripe/billing code, no plan enforcement, no quota system.

**M-6. `get_meeting` silently swallows Storage signing errors.** `meetings.py`'s `get_signed_recording_url` call is wrapped in a bare `except Exception: pass` — if signing fails (expired service key, bucket misconfig, transient network issue), the meeting just renders with no playback URL and **no log line, no error surfaced anywhere**. A systemic Storage problem would be invisible until someone notices "audio just isn't playing."

### Low

**L-1. `search_by_speaker`'s `ILIKE` pattern doesn't escape `%`/`_` in user input.** Not a SQL-injection risk (parameterized via SQLAlchemy), but a speaker name containing those characters silently changes match semantics instead of matching literally.

**L-2. Dashboard swallows fetch failures into an indistinguishable-from-empty state.** `Dashboard.jsx`'s `fetchMeetings` catch block only `console.error`s; the component has an unused `error` state variable. A failed fetch (expired session, network blip, 500) renders identically to "you have no meetings."

**L-3. All-day calendar events are parsed as local midnight**, so `Upcoming.jsx` can display the wrong calendar day depending on the viewer's timezone (west of UTC). Low impact — these events aren't actionable in the UI anyway.

**L-4. `AudioPlayer`'s `audio.play()` promise is unhandled.** A rejected play (autoplay policy, or the signed URL — which expires after 1 hour — going stale on a long-open tab) surfaces only as an uncaught console error, with `isPlaying` state left stuck.

**L-5. Zoom auth state is never validated at startup**, unlike Google's. `assertAuthStateExists()` supports a `platform` argument but `index.js` always calls it with none, defaulting to Google only — a broken/missing `ZOOM_AUTH_STATE_PATH` only surfaces on the first real Zoom join or keepalive cycle, not at boot.

**L-6. Cleanup-only backlog.** ✅ Fixed: the ~100 lines of commented-out dead code in `pdf_service.py`; the stale `supabase/migrations/create_meetings.sql` (removed, with its README and bootstrap-migration references updated); `AUTH_STATE_PATH`/`ZOOM_AUTH_STATE_PATH` now documented in `meeting-bot/.env.example`; unused imports (`inspect` in `chat_service.py`, `Optional` in `auth.py`, `BackgroundTasks` in `meetings.py` and `webhooks.py`); stale local debug screenshots and `backend/test.db` deleted. Still open: `build-essential` remains in the final backend image (no multi-stage split); `pip-audit`/frontend `npm audit` have never been run as a repeatable process (meeting-bot's was run and fixed once).

### Checked and found NOT to be a problem (worth recording so it isn't re-flagged)

- **`ChatMessage` rows do NOT orphan when a meeting is deleted** — `ChatMessage.meeting_id` has `ForeignKey(..., ondelete="CASCADE")` (`backend/app/db/models.py:112`), confirmed directly. An earlier draft finding suggesting an orphaned-row leak here was wrong; the DB-level cascade handles it.
- Join-flow's 8–19s of fixed `waitForTimeout` sleeps in `GoogleMeetBot.js`/`ZoomBot.js` are unchanged — **this is expected**, not a regression: previously declined as too risky to touch (see project memory).

---

## 3. What's already been fixed (confidence-building)

Since the 2026-09-04 audit, 12 of that report's 17 punch-list items were
independently confirmed fixed on current `main`, with tests that were
deliberately *falsified* (reverted, shown to fail) rather than only
code-reviewed: the event-loop-blocking chat tool calls, the missing
`meeting_chunks` index, chat streaming, the Alembic bootstrap gap, the
BrowserManager Chrome leak, the `pactl` blocking call, Dashboard staleness,
dead `teams` code, dead config knobs, `AuthContext` memoization, background-tab
polling, and the outdated `alembic` pin. Security-relevant fixes from the
older audit round also confirmed still in place: RLS policies on all three
tables, the SSRF-safe `meeting_url` host matching, bounded chat request
fields, the Zoom host re-check, gated debug screenshots, UUID-typed path
params, and the DNS-fallback fix for meeting-bot uploads. The upload
retry/recovery work (`retry → uploading → completed`) was verified live
against real Supabase storage, not just in tests.

---

## 4. Deploy readiness verdict

**Not fully production-ready. Deployable for a cautious, low-traffic single-instance launch only after fixing the meeting-bot shutdown gap (C-1).**

Reasoning:

- The **backend's correctness core is genuinely production-grade**: atomic
  scheduler claims, a durable transcription queue with proven crash recovery,
  locally-verified JWTs with a tested forgery defense, a DB pool sized and
  proven against real exhaustion with 503-not-500 handling, RLS on every
  table, no SQL injection, no secrets in git. This isn't a marketing claim —
  every one of those was verified against falsified tests or a live run, not
  just read from code.
- **C-1 (no graceful bot shutdown) is the one finding that should block
  launch on its own.** It's not a rare edge case — it fires on every bot
  deploy while anyone is recording, silently destroys the recording, and the
  user doesn't find out for up to ~105 minutes. For a recording product,
  losing the recording is the worst possible failure mode, and this one is
  guaranteed to recur on a normal deploy cadence.
- **H-1 (no rate limiting) and H-2 (indexing-failure dead end) are real but
  survivable for a soft launch** if traffic is low and monitored — H-1 is a
  cost-abuse risk that scales with usage, H-2 affects only the meetings that
  hit that specific failure path (rare, per the "0 failed jobs observed" note
  in scaling-plan.md, but with zero recovery when it does happen).
- **H-3 and M-1–M-4 are hardening, not blockers** — they degrade gracefully
  today (watchdog TTLs, existing pool 503 handling) rather than corrupting
  data, but should be closed soon after launch.
- **Phase C (multi-host bot pool) not being started is correctly not a
  blocker** — the scaling plan's own target deployment is a single EC2
  instance, and C1/C2 already turned "meeting rejected, lost" into "meeting
  waits," which is the failure mode that actually matters at that scale.

### Recommended order of fixes before launch

1. **C-1** — add SIGTERM/SIGINT handling to meeting-bot (graceful ffmpeg stop, webhook notification, browser cleanup); verify the Docker entrypoint actually delivers the signal (invoke `node` directly instead of `npm start`, or add `tini`).
2. **H-1** — basic per-user rate limiting on `/chat/stream` and meeting creation (Redis is already there from A3).
3. **H-2** — give indexing-failure meetings a real recovery path: either let `/retry` accept a `completed`-but-unindexed meeting, or add a UI affordance for it. At minimum, surface the failure in the UI instead of silent empty chat results.
4. **H-3** — apply the same connection-release pattern to the 5 remaining routes; it's a known fix, already done three times.
5. **M-1, M-2** — add Docker healthchecks; fail loudly at startup if `FRONTEND_ORIGIN` is left at its dev default in a non-development environment.
6. Everything else in §2 Medium/Low as ongoing hardening, not launch gates.

---

## 5. Sources reviewed

`docs/scaling-plan.md`, `docs/full-audit-2026-09-04.md`, `docs/final-audit.md`,
`docs/api-auth-audit.md`, `docs/rls-audit.md`, `docs/validation-audit.md`,
`docs/secrets-audit.md`, `docs/docker-audit.md`,
`docs/backend-runtime-writes-audit.md`, `docs/reliability-audit.md`,
`docs/deploy-readiness.md`, `docs/auth-keepalive-runbook.md`, plus direct
reading of current source across `backend/`, `frontend/`, `meeting-bot/`, and
`supabase/migrations/`, and `git log` for what's landed since each prior
audit. No code was executed, built, or tested as part of this review.
