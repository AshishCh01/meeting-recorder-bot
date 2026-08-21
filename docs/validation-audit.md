# Input Validation Audit

Date: 2026-08-21
Scope: every FastAPI endpoint (`backend/app/api/**`), the request models that back them (`backend/app/models/meeting.py`), and the meeting-bot Express API + Playwright bot classes (`meeting-bot/src/**`) that FastAPI calls into. Focus: the meeting URL field's validation before reaching Playwright, user-supplied strings reaching a shell command / file path / DB query, and missing length/type constraints on request bodies.

Status: report only. No fixes applied.

---

## 1. Meeting URL — not validated as a real meeting link anywhere in the chain (High)

Traced the full path from user input to browser navigation:

| Stage | File:Line | What it actually checks |
|---|---|---|
| Frontend form | `frontend/src/pages/Dashboard.jsx:91-96` | HTML5 `type="url" required` only — client-side, trivially bypassed by calling the API directly (curl/Postman) |
| Request model | `backend/app/models/meeting.py:5-6` | `MeetingCreate.meeting_url: str` — plain string, no `pydantic.HttpUrl`, no regex, no domain allowlist, no length limit |
| Platform routing | `backend/app/services/platform_detector.py:1-9` | `detect_platform()` does a **substring check** (`"meet.google.com" in url.lower()`) — this decides which *bot handler* processes the request, it does not validate the URL's structure, scheme, or host at all |
| Backend → meeting-bot | `backend/app/services/bot_service.py:11-32` | Forwards `meeting_url` verbatim as `url` in the JSON body — no re-validation |
| meeting-bot API | `meeting-bot/src/api/server.js:47-50` | `if (!url || !meetingId || !userId)` — truthiness only, no format/type/length check on any of the three |
| Google Meet bot | `meeting-bot/src/platforms/google-meet/GoogleMeetBot.js:29` | `await this.page.goto(this.session.meetingUrl, ...)` — **the raw, attacker-suppliable string is passed directly to Playwright's browser navigation with zero validation at this final step either** |

**Net result: nothing in this entire chain actually confirms the URL is a real Google Meet link before a live, authenticated Playwright browser navigates to it.** `detect_platform`'s substring check is trivially satisfied by any string that merely *contains* `meet.google.com` anywhere — e.g. as a query parameter or URL fragment on a completely different host: `https://attacker.example/x?y=meet.google.com` passes the check and is routed to the Google flow, and `page.goto()` then navigates to `https://attacker.example/x?y=meet.google.com` verbatim, not to Google.

**Who can trigger this:** any authenticated app user, via the normal `POST /meetings` endpoint — no elevated privilege needed. `create_meeting` (`meetings.py:32-63`) only requires a valid Supabase session and calls `detect_platform()` on whatever string the caller sends as `meeting_url`.

**Impact:** this is a real SSRF primitive. The meeting-bot container's Playwright browser can be made to navigate to any URL an authenticated user chooses (as long as it contains one whitelisted substring somewhere), including:
- Other services on the Docker network (e.g. `http://backend:8000/...` inside the `meeting-net` compose network), bypassing whatever external-facing network boundary normally protects them.
- Cloud instance-metadata endpoints if deployed on infrastructure that exposes one, a classic SSRF-to-credential-theft escalation path.
- Any external site, rendered inside the bot's real browser context and screenshotted (`GoogleMeetBot.js:36,58,67,89` all take screenshots mid-flow) — a secondary content-exposure vector if those screenshots are ever retrieved.

Cookie/session theft of the bot's Google account is **not** directly possible this way (browser cookies stay scoped to their own origin regardless of navigation target), but the SSRF/internal-reachability risk stands on its own.

- **Severity: High.** Exploitable by any authenticated user with no additional access required; impact depends on deployment network topology, up to Critical if an internal service or metadata endpoint is reachable from the meeting-bot container.

### The Zoom path is accidentally safer (worth noting, not a fix needed)
`ZoomBot.buildDirectWebClientUrl()` (`ZoomBot.js:14-26`) always rewrites the navigation target to a hardcoded `https://app.zoom.us/wc/{id}/join` regardless of the input host — so even if `detect_platform`'s weak check lets a non-Zoom URL through to the Zoom flow, the actual `page.goto()` destination is forced back to `app.zoom.us`. This isn't intentional hardening against the routing weakness (it's just how the direct-web-client URL is constructed), but it means the Google Meet path above is the one that actually matters here — Zoom doesn't have the same exposure.

### Teams: not actually implemented
`detect_platform` accepts `teams.microsoft.com`/`teams.live.com` and `server.js` registers `/teams/join`, but `MeetingLifecycle.js:2-3` only maps `google`/`zoom` platforms to bot classes — a Teams URL fails at `BotClass` lookup (`Unsupported platform: teams`) and the meeting is marked failed. Functional gap, not a security finding, but worth knowing if Teams support is ever added, it needs the same URL-validation fix as Google Meet.

---

## 2. Shell commands (Low — structurally safe, but see the file-path finding below)

`meeting-bot/src/recording/FFmpegManager.js:32` is the only `child_process` usage in the repo (`spawn('ffmpeg', args, ...)`, confirmed via a repo-wide grep for `exec(`, `execSync(`, `spawn(`). It uses array-form `spawn()` with `stdio` piping and **no `shell: true`**, so `args` are passed as discrete argv entries, never through a shell — classic shell-metacharacter injection (`;`, `|`, `` ` ``, `$()`) is not possible here regardless of what any individual argument contains.

**Severity: none for shell injection specifically.** The real risk in this file is what `outputPath` (one of those args) is set to — see next section.

---

## 3. File paths — meeting-bot's `meetingId` is trusted with no format validation (Medium)

`meeting-bot/src/recording/RecordingFile.js:7-12`:
```js
static pathFor(meetingId) {
  ...
  return path.join(RECORDINGS_DIR, `${meetingId}.m4a`);
}
```
`meetingId` (from the `/google|zoom|teams/join` request body, `server.js:47`) is interpolated directly into a filename with no validation that it's UUID-shaped or free of path separators/`..` sequences. `path.join` does **not** stop `..` segments from escaping `RECORDINGS_DIR` — a `meetingId` like `../../../../some/other/path` would resolve outside the intended directory, and that path becomes `outputPath` passed straight into `FFmpegManager`'s `spawn()` args (`FFmpegManager.js:6,27`) — i.e. an arbitrary-file-write primitive via ffmpeg's output destination.

`storageKeyFor(userId, meetingId)` (`RecordingFile.js:14-17`) similarly interpolates both `userId` and `meetingId` unvalidated into the Supabase Storage object key. Storage keys are a virtual namespace (no real directory traversal semantics like a filesystem), but a mismatched/attacker-chosen `userId`/`meetingId` pair could still write a recording under a different tenant's storage prefix.

**Is this reachable today?** Not by an external attacker directly — `/google/join` etc. require the shared bearer secret (`requireAuth`, `server.js:30-43`), and the only real caller, `bot_service.py:11-32`, always sends `meetingId = str(meeting.id)`, a server-generated UUID4 that's never client-influenced. So in the current call graph, `meetingId` is always well-formed.

**Why it's still a finding:** the meeting-bot API has **no validation of its own** at this boundary — it relies entirely on (a) the bearer secret never leaking and (b) the backend never having a bug that forwards a malformed id. That's a single point of failure with no defense-in-depth. Given this repo has already had one credential leak incident (see `docs/secrets-audit.md` — `auth.json` was committed to git history), relying solely on a shared secret never leaking is not a comfortable assumption.

- **Severity: Medium.** Not exploitable under normal operation; becomes a real arbitrary-file-write / cross-tenant-storage-write bug if the bearer token is ever compromised or the backend ever forwards a bad id. Recommend validating `meetingId`/`userId` are UUID-shaped at the top of `makeJoinHandler` in `server.js`, independent of what the backend currently sends.

---

## 4. Database queries — parameterized everywhere, one LIKE-wildcard gap (Low)

Grepped `backend/app` for raw SQL construction (`text(`, f-string/`.format()`/`%`-formatting feeding `.execute()`) — the only `.execute()` calls found are Alembic-style `sqlalchemy.update()`/`insert()` constructs (`webhooks.py`, `meetings.py`) or the migration files, all using SQLAlchemy's expression language with bound parameters. No hand-built SQL strings anywhere in the application code. **No SQL injection found.**

One related-but-distinct issue: `backend/app/rag/tools.py:91` —
```python
func.array_to_string(MeetingChunk.speakers, ',').ilike(f"%{speaker_name}%")
```
`speaker_name` (an LLM tool-call argument derived from the user's chat question, not itself a raw HTTP parameter) is embedded into an f-string that becomes the *value* passed to `.ilike()` — this is still sent as a bound parameter by SQLAlchemy, so it's **not SQL injection**. But `speaker_name` isn't escaped for SQL `LIKE` wildcard characters (`%`, `_`) before being wrapped. A speaker name containing `%` would broaden the match beyond the intended speaker (e.g. `%` alone matches every chunk with any non-null speaker). Since this query is already scoped to the caller's own meeting (`Meeting.user_id == user_id` checked at `tools.py:81` first), the impact is limited to a user getting broader results from their *own* meeting data than the query implies — not a cross-user leak.

- **Severity: Low.** Recommend escaping `%`, `_`, and `\` in `speaker_name` before building the pattern, or using a parameterized `ILIKE ANY(speakers)` array-membership check instead of string-flattening.

---

## 5. Missing length/type constraints on request bodies (Low–Medium)

| Model / field | File:Line | Gap |
|---|---|---|
| `MeetingCreate.meeting_url` | `models/meeting.py:5-6` | Plain `str`, no `HttpUrl` type, no `max_length`, no domain allowlist — root cause of finding #1 |
| `ChatRequest.question` | `models/meeting.py:20-22` | Plain `str`, no `max_length` — an authenticated user can send an arbitrarily long question, which gets forwarded to the Gemini agent (`chat_service.py`) and to embedding calls (`search_transcript`, `tools.py:147`) — a cost/DoS vector, not a correctness bug |
| `ChatRequest.session_id` | `models/meeting.py:22` | `Optional[str]`, no format constraint (e.g. UUID) despite being used as a session-cache key |
| `RecordingCompleteWebhook.recording_path` / `error_message` | `models/meeting.py:35-36` | `Optional[str]`, no length limit — lower priority since this endpoint is bearer-token-gated, service-to-service only |
| meeting-bot `url`/`meetingId`/`userId` | `server.js:47-50` | Checked for truthiness only — no type, length, or format constraint of any kind (covers finding #1 and #3 from the meeting-bot side) |

- **Severity: Low–Medium**, scaled to each field's blast radius — `meeting_url` is the highest-impact instance (already covered under finding #1); the rest are mainly cost/robustness concerns, not authorization bypasses.

---

## Severity summary

| Finding | Severity | Exploitable by |
|---|---|---|
| `meeting_url` not validated as a real meeting link anywhere in the chain → SSRF via `page.goto()` | **High** | Any authenticated app user, no extra privilege needed |
| meeting-bot API trusts `meetingId`/`userId` format with no independent validation → path traversal / cross-tenant storage write if the shared bearer secret is ever compromised | **Medium** | Requires bearer-token compromise or a bug in the trusted backend caller — not reachable today |
| Shell command construction (`ffmpeg` via `spawn`) | **None** | Array-form spawn, no shell, structurally safe |
| SQL injection in ORM queries | **None found** | All queries parameterized via SQLAlchemy |
| LIKE-wildcard injection in `search_by_speaker` | **Low** | Same authenticated user only, broadens results within their own data |
| Missing length/type constraints (`ChatRequest.question`, `session_id`, meeting-bot body fields) | **Low–Medium** | Any authenticated user; mainly cost/DoS/robustness, not an authorization bypass |

## Recommended next steps (not performed — report only)
1. Validate `meeting_url` server-side in `MeetingCreate` (e.g. `pydantic.HttpUrl` plus a real host allowlist — `urlparse(url).hostname` matched against `{"meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com"}`, not a substring check) before it ever reaches `detect_platform()`, and reject anything that doesn't parse as a well-formed HTTPS URL on one of those hosts.
2. Add the same host-allowlist check independently inside `GoogleMeetBot.join()` (or a shared helper) before `page.goto()`, so the browser-navigation boundary doesn't rely solely on validation done several hops upstream.
3. Validate `meetingId`/`userId` shape (UUID) in `server.js`'s `makeJoinHandler`, independent of what the backend currently sends.
4. Add `max_length` to `ChatRequest.question` and a UUID/format constraint to `session_id`.
5. Escape `%`/`_`/`\` in `speaker_name` before building the `ILIKE` pattern in `tools.py`.
