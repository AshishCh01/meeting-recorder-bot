# Full-Codebase Audit — Bugs, Security, and Latency

Date: 2026-09-04
Scope: full repo (`backend/`, `frontend/`, `meeting-bot/`, `supabase/`), with emphasis on (1) why answering a chat question about a meeting is slow, and (2) anything not already covered by the prior audits in this folder (`final-audit.md`, `api-auth-audit.md`, `rls-audit.md`, `validation-audit.md`, `secrets-audit.md`, `docker-audit.md`, `backend-runtime-writes-audit.md`, `reliability-audit.md`, `deploy-readiness.md`, `auth-keepalive-runbook.md`, all dated 2026-08-21 through 2026-08-24). This report does not repeat those findings in full — it verifies which of their ship-blockers are actually fixed as of the current `main` branch, and covers ground they didn't: the Google Calendar/OAuth integration, `AuthKeepAlive.js`, and — the main ask — end-to-end RAG chat latency.

Status: **report only. Nothing in this document has been fixed yet.**

---

## 0. Executive summary

The single biggest, most fixable problem in the codebase is the **RAG chat latency**: every chat request runs blocking database queries and a blocking Gemini embedding call directly on the backend's single async event loop, which means one user asking a question about their meeting measurably slows down **every other concurrent request to the entire backend**, not just their own. This gets worse under any real load. There's also a dropped index that makes the vector search scan across every meeting in the system, not just the one being asked about — so latency will keep climbing as total data grows, independent of any one meeting's size.

Security-wise, the app's core authorization model (ownership checks, RLS, bearer-token auth, input validation) was already solid per the prior audits and remains so. The newly added Google Calendar/OAuth integration is well-built (encrypted refresh tokens, signed CSRF-safe state, minimal scopes, correct ownership scoping). Most previously-flagged ship-blockers are now fixed. The two things still open are a real correctness gap (the Alembic migration chain still can't create a fresh `meetings`/`meeting_chunks` table from scratch) and a resource-leak class in the meeting-bot (orphaned Chrome processes on launch failure).

Ranked punch list is in [§6](#6-ranked-punch-list).

---

## 1. RAG chat latency — root cause analysis

This is the direct answer to "it takes too much time to give answer when users ask a question about the meeting." Traced end-to-end: `POST /meetings/{id}/chat` (`backend/app/api/chat.py`) → `chat_service.ask_question` (`backend/app/rag/chat_service.py`) → tool calls (`backend/app/rag/tools.py`) → embedding (`backend/app/services/embedding_service.py`).

### 1.1 Blocking sync calls run directly on the async event loop — highest impact

`backend/app/rag/tools.py:28,55,78,138` (`get_meeting_summary`, `get_action_items`, `search_by_speaker`, `search_transcript`) are plain synchronous functions that open a sync SQLAlchemy session and query the DB. `chat_service.py:192-203`'s tool-dispatch loop calls them **directly inside `async def ask_question`** — no `asyncio.to_thread` / `run_in_executor`.

Worse, `search_transcript` (`tools.py:147`) calls `embed_query()` → `embedding_service.py:145-166` → the **synchronous** `genai` client (`client.models.embed_content`, not `client.aio`), a real ~100-500ms network round-trip executed inline on the event loop.

Because asyncio is single-threaded, **every concurrent request to the entire FastAPI app — any user, any meeting, any endpoint — stalls for the full duration of this DB query + embedding call** while one user's tool call runs. Multiple tool calls within one turn also run sequentially in a Python `for` loop (`chat_service.py:182-209`), not concurrently, compounding this.

**Impact:** 100ms–1s+ of event-loop-wide blocking per tool call, and it degrades everyone's requests, not just the asker's.

### 1.2 Blocking `time.sleep` in the embedding retry path

`embedding_service.py:118` uses `time.sleep(delay)` (not `asyncio.sleep`) with exponential backoff across up to 4 attempts. On a transient 429/503 from Gemini's embedding API, this can freeze the **entire server** for ~7–8s or more. (The main generation retry loop, `chat_service.py:160`, correctly uses `asyncio.sleep` — only the embedding path has this bug.)

### 1.3 No index on `meeting_chunks.meeting_id` — vector search scans system-wide, not per-meeting

`backend/alembic/versions/475e9ee29ad9_initial_schema.py:55` explicitly **drops** `meeting_chunks_meeting_id_idx`, and `c3d9f2a1b4e7_create_meeting_chunks_table.py:19-20` documents this as intentional ("to match the live schema as-is") — it's never recreated. The only index present is the global `ivfflat` ANN index on `embedding` (`vector_cosine_ops`).

`tools.py:149-154` runs `WHERE meeting_id = X ORDER BY embedding <=> query_embedding LIMIT top_k`. With no index on `meeting_id`, and ivfflat having no efficient pre-filter support, Postgres either scans the ANN index globally across **every meeting's chunks in the whole system** and post-filters, or falls back to a full sequential scan. This is not the "fetch everything into Python" anti-pattern (the SQL-side `cosine_distance()` usage is correct) — but it is not scoped to one meeting, so **latency scales with total chunks system-wide**, and will keep getting worse as more meetings accumulate, regardless of which meeting is being asked about.

### 1.4 Multi-turn LLM round-trips with no streaming

`chat_service.py:134-226` allows up to 6 tool-calling round-trips (`MAX_TOOL_ITERATIONS = 6`, line 118). A typical specific question needs at least 2 full Gemini round-trips (decide to call a tool, then synthesize the answer); multi-part questions can hit 3–4, since the tool docstring (`tools.py:127-128`) explicitly encourages re-searching. None of this is streamed to the client — `client.aio.models.generate_content` is a single blocking-until-complete call, and `chat.py:27-28` awaits the full result before responding. **The user sees nothing until the entire multi-turn loop finishes.**

### 1.5 Verified fine — not contributors

- **DB connection pooling** (`database.py:14-20`): `pool_pre_ping=True`, `pool_size=10`, `max_overflow=20`, created once at module scope. Fine.
- **Session lock** (`chat_service.py:20-29`): keyed per `user_id:meeting_id:session_id`, so it only serializes a user's own back-to-back messages, not other users. Not the real bottleneck (§1.1 is).
- **Model choice** (`config.py:19,24`): both `gemini_model` and `rag_agent_model` default to `gemini-3.6-flash`, not a slower "pro" tier. Fine.
- **Retrieval size** (`config.py:27`): `retrieval_top_k=6` × ~500-token chunks ≈ ~3k tokens of injected context — reasonable, not bloated.
- **Indexing/transcription**: runs in a background `ThreadPoolExecutor` (`transcription_service.py:12`), fully decoupled from the chat request path — does not slow down chat.
- **Client/engine init**: `genai.Client` and the DB engine are both constructed once at import time, not per-request.
- Dead config note (not a latency bug): `settings.chunk_segments`/`chunk_overlap` (`config.py:25-26`) are declared but never read — `embedding_service.py`'s `_build_chunks` uses hardcoded `TARGET_TOKENS=500`/`OVERLAP_TOKENS=50` instead. Misleading if someone tunes those env vars expecting an effect.

### Ranked fixes for latency (highest impact first)

1. **Move every tool body off the event loop.** Wrap the sync DB-query tool functions in `asyncio.to_thread(...)` (or run them via an executor) instead of calling them inline in `chat_service.py:192-203`, and switch `embedding_service.py`'s Gemini embed call to the async client (`client.aio.models.embed_content`) to match the pattern already used for generation. This is the fix that stops one user's question from slowing down the whole app.
2. **Restore a `meeting_id` index on `meeting_chunks`** (a new migration re-adding `CREATE INDEX ON meeting_chunks (meeting_id)`, or a partial/composite ANN index), so `search_transcript` doesn't scan across every meeting in the system on every question.
3. **Replace `time.sleep` with `asyncio.sleep`** in `embedding_service.py:118`'s retry loop — currently can freeze the entire server for 7s+ on a transient embedding-API error.
4. **Stream the final answer** (`generate_content_stream` + a streaming HTTP response) instead of blocking on the whole multi-turn loop before returning anything — the biggest perceived-latency win for the common 2–3 round-trip case.
5. (Minor) Wire up `chunk_segments`/`chunk_overlap` into `_build_chunks`, or remove them from `config.py` since they currently do nothing.

**Files to touch:** `backend/app/rag/chat_service.py:44-226`, `backend/app/rag/tools.py:18-175`, `backend/app/services/embedding_service.py:96-166`, `backend/app/api/chat.py:12-28`, a new Alembic migration.

---

## 2. Security — new surface and residual findings

### 2.1 Google Calendar / OAuth integration (new since last audit) — clean

- **Token storage:** refresh tokens are Fernet-encrypted before storage (`google_oauth_service.py:50-55`); the DB column is literally named `refresh_token_encrypted` (`db/models.py:65`). Access tokens are never persisted at all — derived fresh from the refresh token on every use (`calendar_service.py:25-35`). **No plaintext-credential risk.**
- **CSRF (`state` param):** `calendar.py:37` verifies `state` via `oauth.verify_state()`, which is itself a Fernet-encrypted, TTL-bound (600s) token embedding `user_id` — not a guessable/comparable string, can't be forged or replayed after expiry. **Solid.**
- **`redirect_uri`:** hardcoded server-side from `settings.google_oauth_redirect_uri`, never taken from the request. **Not attacker-controllable.**
- **Scopes:** `calendar.readonly` + `userinfo.email` only — minimal, no write access requested. **Correct.**
- **Ownership checks:** every authenticated `calendar.py` endpoint filters by the token-derived `user_id`, matching the pattern used everywhere else in this codebase. The one unauthenticated route (`GET /calendar/oauth/callback`) is unauthenticated by necessity (Google's own redirect hits it) and derives identity solely from the signed `state` — correct design, not a gap.
- **Scheduler (`scheduler.py`):** the background sweep queries meetings globally (expected — it's a sweep), but every per-meeting action scopes strictly to that row's own `user_id`. No cross-user leakage path found.
- **No hardcoded secrets** in any new file (`calendar.py`, `google_oauth_service.py`, `calendar_service.py`, `scheduler.py`, `AuthKeepAlive.js`) — all read from `settings`/`process.env`.

**Verdict: no security issues found in the new Calendar/OAuth/scheduler surface.**

### 2.2 `AuthKeepAlive.js` (new) — clean

Runs every 15 minutes by default (per `docs/auth-keepalive-runbook.md`), loading an authenticated page for Google then Zoom to keep sessions warm. Check URLs are hardcoded constants (`myaccount.google.com`, `zoom.us/profile`), never derived from request input — no SSRF surface. No credentials are ever logged, only generic status strings. Browser contexts are correctly closed in a `try/finally` — **except** see §3.1 below for one real gap in the underlying `BrowserManager.launch()` it depends on.

### 2.3 Residual / latent finding: `teams` platform still dead code, now provably unreachable

`meeting-bot/src/api/server.js:98` still exposes `POST /teams/join`, and `bot_service.py:7` still maps a `/teams/join` endpoint, but `MeetingLifecycle.js`'s `BOT_CLASSES` has no `teams` implementation. However, `platform_detector.py`'s host allowlist now excludes Teams hosts entirely, so no current code path (manual meeting creation or calendar-derived scheduling) can actually produce `platform="teams"` and reach this dead code. **Severity: Low, latent only** — a future code path or a direct call to meeting-bot with the shared bearer token could still trigger it, leaving a meeting stuck in `joining` until the watchdog TTL sweep cleans it up. Recommend removing the route/mapping entirely, or implementing it, rather than leaving reachable dead code that silently breaks if a validation rule elsewhere ever changes.

### 2.4 Dependency scan — inconclusive, run the real tools

Backend (`requirements.txt`): `fastapi==0.141.1`, `pydantic==2.9.2`, `sqlalchemy==2.0.52`, `cryptography==43.0.3` (postdates the CVE-2024-26130 fix), `psycopg[binary]==3.3.4` — no confidently-known CVE against these exact pins. `alembic==1.13.1` is comparatively old and worth bumping for parity, no known critical issue.

Frontend/meeting-bot (`package-lock.json`): `axios` resolves to `1.19.0`, `vite` to `8.2.1`, `follow-redirects` to `1.16.0` (past the CVE-2024-28849 and CVE-2023-26159 fixes), `express` to `4.22.2`, `playwright`/`playwright-core` to `1.62.1` — nothing confidently tied to a known critical CVE at these versions.

**This is not a substitute for actually running `pip-audit` (backend) and `npm audit` (frontend, meeting-bot)** — those tools check against live advisory databases and will catch anything a static read-through can't. Recommend running both as a follow-up; none of this section should be read as "dependencies are clean," only as "nothing jumped out."

### 2.5 Fix-verification of previously reported ship-blockers

| Prior finding | Status now |
|---|---|
| Alembic chain never creates `meetings`/`meeting_chunks` tables (can't bootstrap a fresh DB) | **Still not fixed** — confirmed via grep, no `create_table('meetings'` or `create_table('meeting_chunks'` anywhere in `backend/alembic/versions/`. Both tables were created out-of-band (Supabase dashboard/SQL editor) and are only ever `ALTER`'d in the tracked migration chain. `alembic upgrade head` against a genuinely empty database will still fail. |
| `DELETE /meetings/{id}` didn't stop an in-progress bot | **Fixed.** `meetings.py:183-192` now checks status and calls `stop_bot()` before deleting. |
| Production `backend/Dockerfile` missing `ffmpeg` | **Fixed.** Line 13-16 installs it. |
| `backend/Dockerfile` ran as root (High severity) | **Fixed.** Creates `appuser`, chowns `/app`, switches `USER appuser` before `CMD`. |
| `meeting-bot/auth.json`/`zoom-auth.json` tracked in git | **Fixed (untracked).** Confirmed via `git ls-files` — no longer tracked; `docker-compose.prod.yml` bind-mounts them from the host instead. |
| `teams` platform reachable with no bot implementation | **Downgraded, not fully fixed.** No longer reachable via the app's normal flow (host allowlist blocks it upstream), but the dead route/mapping still exists — see §2.3. |

---

## 3. Meeting-bot — performance and one resource-leak finding

### 3.1 Orphaned Chrome processes on launch failure (real resource leak, compounds over time)

`meeting-bot/src/core/BrowserManager.js:94-127` (`launch()`) has no `try/catch` around the sequence `chromium.launch()` → `browser.newContext()` → `context.addInitScript()` → `return context`. If `newContext()` or `addInitScript()` throws — e.g. a corrupted or stale `auth.json`/`zoom-auth.json` — the already-launched Chrome process is never closed, and no reference to it survives the throw. The caller never receives a `context`, so `bot.leave()`'s `if (this.context)` guard is false and cleans up nothing. **Every join attempt that fails in this narrow window leaks a full headed Chrome process** on a container where the Xvfb display is shared across all sessions — repeated failures (e.g. one stale auth file causing every subsequent join to fail identically) progressively degrade CPU/memory for every other meeting on that container. The exact same gap exists in `AuthKeepAlive.js:56`, which hits this code path every 15 minutes indefinitely.

**Fix:** wrap the launch sequence in try/catch, and on any failure after `chromium.launch()` succeeds, explicitly call `browser.close()` before rethrowing.

### 3.2 Blocking sync subprocess call before every join

`meeting-bot/src/recording/AudioSink.js:22-27` uses `execFileSync('pactl', ...)` — a blocking call on Node's single-threaded event loop — invoked from `MeetingLifecycle.js:22` before `bot.join()` starts. Adds a small serial delay to every join today; becomes a bigger problem if `MAX_CONCURRENT_MEETINGS` is ever raised above the current default of 1, since it would stall every other concurrently-running meeting's async work for its duration.

### 3.3 Several seconds of unconditional fixed sleeps per join

Verified fixed `waitForTimeout` calls with no corresponding wait-condition:
- Google Meet (`GoogleMeetBot.js`): 5000ms (always) + 1000ms (conditional) + 2000ms (always) ≈ **8s baseline per join**.
- Zoom (`ZoomBot.js`): 4000ms (always) + 8000ms (conditional interstitial) + 3000ms (always) + 4000ms (always) ≈ **11s baseline, 19s with interstitial**.

None of this overlaps with other setup work — it's pure serial "let the UI settle" padding. This directly inflates time-to-join and is easy to trim/replace with actual element-ready waits.

### 3.4 Single-active-meeting cap, hard rejection, no queue

`meeting-bot/src/api/server.js:18` — `MAX_CONCURRENT_MEETINGS` defaults to 1; a second concurrent join request gets an immediate `409` rejection, not queued. This is a real "why did my meeting fail to record" and scaling concern for any deployment with more than one simultaneous user, by design (shared Xvfb display and platform auth identity), not a bug — but worth surfacing since it directly affects perceived reliability/latency under load.

### 3.5 Security — re-verified, no regressions

The previously-flagged `BEARER_TOKEN` empty-token bypass (`|| ''` fallback letting an absent token pass `crypto.timingSafeEqual`) is **fixed** — `server.js`'s `timingSafeTokenEqual` now explicitly rejects when either side is falsy before ever reaching the constant-time comparison. UUID validation on `meetingId`/`userId` at the join boundary is intact. No new issues found in the recent "scaling and auth token generation" changes.

---

## 4. Frontend — performance and security

### 4.1 Duplicate, desynced chat state (correctness bug, not just perf)

`MeetingView.jsx:116-122` mounts both `<ChatInterface>` (desktop) and `<MobileChatSheet>` (mobile) unconditionally — the responsive split is CSS-only (`hidden`/`lg:hidden`), so both are always in the DOM. Each independently calls `useMeetingChat(meetingId)` (`hooks/useMeetingChat.js:12`), and despite a code comment claiming the two surfaces share one conversation, they hold **two separate `messages` arrays** via independent `useState`. A message sent on desktop won't appear in the mobile sheet's history (or vice versa), and double the chat hook/component overhead is mounted at all times. **Recommend lifting chat state to a shared hook/context so both surfaces read the same conversation.**

### 4.2 `MeetingView` polls every 5s with no background-tab pause

`MeetingView.jsx:44-51` polls unconditionally every 5s while a meeting is non-terminal (up to the 90-minute recording cap), with no `visibilitychange` check to pause when the tab is backgrounded — up to ~1080 unnecessary requests per session if left open in the background. Cleanup on unmount is correct; this is pure waste, not a leak.

### 4.3 `Dashboard` meeting list never refreshes after initial load

`Dashboard.jsx:44-46` fetches the meeting list once on mount only — a meeting that finishes processing in the background won't update in the list until the user manually reloads the page. (This is the opposite problem from §4.2 — staleness rather than over-polling.)

### 4.4 `AuthContext` value not memoized

`context/AuthContext.jsx:23-27` builds a new `{session, user, signOut}` object on every render (including a fresh `signOut` closure), causing every consumer of `useAuth()` to re-render on any provider re-render. Low impact given how infrequently auth state changes, but a one-line `useMemo`/`useCallback` fix.

### 4.5 Security — no XSS vector found, standard SPA token tradeoff

No `dangerouslySetInnerHTML`/`innerHTML` anywhere in `frontend/src` (verified by full-tree grep). Chat responses render through `ReactMarkdown` with no raw-HTML plugin loaded, so LLM output is markdown-escaped, not injected as HTML; user-typed messages render as auto-escaped plain JSX. **No active XSS path found.**

The Supabase session (access + refresh token) is stored in `localStorage` by default (`lib/supabase.js:6` uses no `auth.storage` override), and `lib/api.js` reads it per-request to attach the bearer header. Since no injectable-HTML path exists today, there's no demonstrated exploit chain — but this remains the standard SPA tradeoff (an httpOnly cookie would be more resilient against a future XSS introduced by a compromised dependency). Flagged as hardening debt, not an active vulnerability. `frontend/.env.example` contains only the expected public Vite build vars — no leaked secrets.

---

## 5. Everything checked and found clean (for completeness)

- SQL injection: none found anywhere — all queries parameterized via SQLAlchemy (re-confirmed).
- Shell/command injection in meeting-bot: `ffmpeg` (`FFmpegManager.js`) and `pactl` (`AudioSink.js`) both use array-form `spawn`/`execFileSync`, never shell-interpolated strings.
- Ownership/authorization: every authenticated endpoint across `meetings.py`, `chat.py`, `calendar.py`, and the RAG tool layer scopes by the token-derived `user_id`; no endpoint trusts a client-supplied identifier for authorization.
- Webhook auth: `verify_webhook_token` uses `hmac.compare_digest` against a required, non-default secret — genuinely validated, not a stub.
- DB connection pooling, client/engine initialization, session-lock scoping, model tier choice, retrieval context size — all reviewed in §1.5, no issues.
- No hardcoded secrets in any file touched since the last audit round.

---

## 6. Ranked punch list

### Fix first (highest impact on the reported problem — chat is slow)
1. Offload sync tool bodies + the sync embedding call off the event loop (`asyncio.to_thread` / async Gemini client) — stops one user's question from slowing down every other request. (§1.1)
2. Restore an index on `meeting_chunks.meeting_id` — stops vector search from scanning system-wide as data grows. (§1.3)
3. Replace `time.sleep` with `asyncio.sleep` in the embedding retry path — stops a transient API error from freezing the whole server for 7s+. (§1.2)
4. Stream the final chat answer instead of blocking on the full multi-turn loop. (§1.4)

### Should-fix (correctness/reliability, not launch-blocking)
5. Alembic migration chain still can't bootstrap `meetings`/`meeting_chunks` from an empty database — add the missing `CREATE TABLE` migrations. (§2.5)
6. Fix orphaned-Chrome-process leak in `BrowserManager.launch()` — wrap in try/catch, close the browser on any failure after launch. (§3.1)
7. Unify chat state between `ChatInterface` and `MobileChatSheet` so desktop/mobile share one conversation. (§4.1)
8. Trim the 8–19s of unconditional fixed sleeps in the Google Meet / Zoom join flows. (§3.3)
9. Move `pactl` provisioning off the event loop if `MAX_CONCURRENT_MEETINGS` is ever raised above 1. (§3.2)
10. `Dashboard.jsx` never refreshes the meeting list after mount — add polling or a refresh trigger. (§4.3)
11. Remove the dead `teams` route/mapping (`server.js`, `bot_service.py`, `MeetingLifecycle.js`) rather than leaving unreachable-but-present code that breaks silently if the upstream allowlist ever changes. (§2.3)

### Cleanup-only
12. Run `pip-audit` and `npm audit` for an authoritative dependency-vulnerability check — this report's manual read-through is inconclusive, not a clean bill of health. (§2.4)
13. Wire up or remove the dead `chunk_segments`/`chunk_overlap` config knobs. (§1.5)
14. Memoize `AuthContext`'s provider value. (§4.4)
15. Pause `MeetingView`'s 5s polling when the tab is backgrounded. (§4.2)
16. Consider moving the Supabase session out of `localStorage` (e.g. httpOnly cookie) as XSS-defense-in-depth, even though no active XSS path exists today. (§4.5)
17. Bump `alembic` to a current version for parity with the rest of the pinned dependencies. (§2.4)
