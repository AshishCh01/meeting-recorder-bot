# Reliability &amp; Production-Failure Audit

Date: 2026-08-21
Scope: `backend/app/**` and `meeting-bot/src/**` — unhandled promise rejections, missing try/catch around external calls, race conditions, resource leaks, and silently-swallowed failures. Findings are ordered with **anything that can permanently strand a meeting in a non-terminal status (`joining`/`recording`/`uploading`/`transcribing`) first**, regardless of other severity factors — that's the single worst failure mode for this app, since the only recovery UI (`POST /meetings/{id}/retry`) only accepts meetings already at `status == "failed"`.

Status: report only. No fixes applied.

---

## Architectural root cause (read this first)

Findings #1–#3 below all trace back to the same gap: **every terminal state transition for a meeting depends on exactly one fire-and-forget call — either the `notifyBackend` webhook from meeting-bot, or a `ThreadPoolExecutor` task in the backend — with no durable retry queue and no watchdog anywhere that revisits a meeting that never heard back.** There is no reconciliation job (confirmed via a repo-wide grep for `stale|watchdog|APScheduler|cron|sweep|reconcile` — no matches). If any one of those single calls fails or hangs, the meeting's DB row is frozen at whatever status it last reached, forever, with no automated or even manual (short of a raw DB `UPDATE`) way to recover it. A single fix — a periodic backend sweep that fails any meeting sitting in a non-terminal status past a sane TTL — would neutralize #1, #2, and #3 simultaneously. See "Recommended next steps" at the bottom.

---

## 1. `notifyBackend` gives up after 3 retries with no persistence (Critical)

**File:** `meeting-bot/src/core/MeetingLifecycle.js:81-107`
**Strands a meeting: YES — by the code's own admission.**

`notifyBackend` is the *only* code path that ever tells the backend a meeting moved past `joining`/`recording`/`uploading`. It retries 3 times (3s/6s backoff, ~9s total) against `BACKEND_WEBHOOK_URL`, and if all 3 fail, it logs `"[Lifecycle] All webhook attempts failed — meeting may be stuck in DB"` and returns — no re-queue, no disk persistence, no later retry of any kind.

Realistic triggers:
- Backend is briefly down/restarting for &gt;9s when the bot finishes recording (plausible during any deploy).
- `BACKEND_WEBHOOK_URL`/`BEARER_TOKEN` misconfigured in meeting-bot's env — deterministic failure, every meeting run on that instance gets stuck.
- A network blip lasting more than ~9s between the two services.

**Compounding gap:** `/meetings/{id}/retry` (`backend/app/api/meetings.py:112-154`) only operates `WHERE Meeting.status == "failed"`. A meeting stuck at `joining`/`recording`/`uploading` from this bug isn't `"failed"`, so retry returns 409 and can't touch it. No operator-facing recovery exists.

- **Severity: Critical.** This is the most direct, most easily-triggered path to a permanently stuck meeting in the whole system.

---

## 2. Unprotected tempfile write, silently swallowed by a fire-and-forget executor (High)

**File:** `backend/app/services/transcription_service.py:188-191`
**Strands a meeting: YES.**

```python
suffix = os.path.splitext(storage_path)[1] or ".m4a"
with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
    tmp.write(file_bytes)
    tmp_path = tmp.name
```

This sits between the download's `try/except` and the large inner `try/except/finally` that handles Gemini/Sarvam failures — it isn't covered by either. If `NamedTemporaryFile()`/`tmp.write()` raises (disk full, permission error — realistic on a container with a small `/tmp`), the exception propagates straight out of `transcribe_recording`.

Both call sites (`webhooks.py:62-66`, `meetings.py:148-152`) submit this function to `transcription_executor` and **never call `.result()` or attach a done-callback** on the returned `Future`. Per `concurrent.futures` semantics, an exception raised inside a submitted callable is captured on the `Future` and silently discarded if nobody ever inspects it — no log, no crash, nothing.

The meeting's status is already committed to `"transcribing"` *before* the submit — so this gap's final state is `"transcribing"` forever, with the same `/retry`-can't-help problem as finding #1.

**Structural note:** this is a narrow trigger today, but the underlying pattern — fire-and-forget `.submit()` with no exception harvesting anywhere — means any future code added outside the well-covered inner `try/except` silently reintroduces this bug class.

- **Severity: High.** Same stuck-forever outcome as #1, narrower trigger condition (needs a local resource failure, not just a network blip).

---

## 3. `bot.leave()` / `context.close()` has no timeout (Medium)

**Files:** `meeting-bot/src/core/MeetingLifecycle.js:46-47`, `BrowserManager.js:37-41`, `GoogleMeetBot.js:173-179`, `ZoomBot.js:195-201`
**Strands a meeting: YES (lower probability, but real).**

`await bot.leave().catch(() => {})` only guards against *rejection*, not a Promise that never settles. Playwright's `context.close()` has no explicit timeout here. If the underlying Chromium process is wedged (crashed-but-not-reaped, unresponsive CDP — plausible under memory pressure running headed Chrome via Xvfb), this `await` can hang indefinitely.

Contrast with `FFmpegManager.stop()` (`FFmpegManager.js:52-90`), which is properly bounded: graceful `q` → 8s timeout → `SIGINT` → 3s timeout → `SIGKILL`. `bot.leave()` has no equivalent escalation.

Because `bot.leave()` runs first inside the lifecycle's `finally`, a hang there blocks everything downstream — FFmpeg stop, upload, and `notifyBackend` never execute. Worse: `server.js`'s single-active-meeting guard (`activeMeeting`, `server.js:17,65-78`) only resets in `.finally()` once `runMeetingLifecycle` *settles* — so a hang here also makes the entire bot instance permanently unable to accept any new meeting, not just the stuck one.

- **Severity: Medium.** Lower likelihood than #1/#3, but the blast radius is worse (takes down the whole bot instance, not just one meeting).

---

## 4. `fs.unlinkSync` failure mislabeled as upload failure (Low)

**File:** `meeting-bot/src/core/MeetingLifecycle.js:57-69`
**Strands a meeting: NO — self-heals via `/retry`, but misreports state.**

```js
try {
  await SupabaseUploader.upload(localPath, storageKey);
  fs.unlinkSync(localPath);   // <- inside the same try as the upload
  session.markCompleted();
} catch (uploadErr) {
  session.markFailed(`Upload failed: ${uploadErr.message}`);
}
```

If cleanup (`fs.unlinkSync`) throws *after* a successful upload — e.g. a Windows file-locking conflict with ffmpeg not yet having released the handle, which this codebase's own comments confirm is a real concern on Windows dev — the meeting gets reported `"failed"` with a "Upload failed" message even though the recording is safely in Supabase. `/retry` will find the file and recover it, but only if someone thinks to click retry on what looks like a genuine upload failure.

- **Severity: Low.** Recommend moving `fs.unlinkSync` (and its own try/catch, since cleanup failure shouldn't fail the meeting either way) outside the upload's try block.

---

## 5. Ack-timeout race → status flip-back → possible duplicate transcription (Medium)

**Files:** `backend/app/api/meetings.py:55-63`, `backend/app/api/webhooks.py:35-67`
**Strands a meeting: NO — but risks a data-consistency bug.**

`trigger_bot_join` has `timeout=10` (`bot_service.py:19-31`). If meeting-bot accepts the job (202) but the HTTP response is lost/delayed past 10s, the backend marks the meeting `"failed"` on the `except` path even though the bot is, unknown to the backend, still actually recording.

The webhook's update filter — `WHERE status NOT IN ('transcribing', 'completed')` (`webhooks.py:41-56`) — does **not** exclude `"failed"`, so when the real recording finishes, the webhook flips the row from `"failed"` back to `"transcribing"` and starts transcription. That's a reasonable self-heal for the terminal-state mismatch by itself, but it opens a window where a user sees `"failed"`, clicks `/retry` (which itself submits a `transcribe_recording` task), and the legitimate webhook's transcription submission lands moments later — **two concurrent `transcribe_recording` runs for the same `meeting_id`**, both writing `meeting.transcript`/`meeting_chunks`. `index_transcript`'s delete-then-insert pattern (`embedding_service.py:187-211`) isn't atomic across the two commits, so interleaved writers could leave a meeting with partial or duplicate chunks.

- **Severity: Medium.** Not a stuck state, but a real data-integrity race under a specific timing window.

---

## Lower-priority / informational

| # | Finding | File:Line | Severity |
|---|---|---|---|
| 6 | `runMeetingLifecycle(session).catch(...).finally(...)` in `server.js` is correctly wired (not an unhandled-rejection bug) but only resets `activeMeeting` — it has no fallback to touch the backend DB itself if the inner lifecycle's own `finally` fails to reach `notifyBackend` (same root cause as #1/#3, not a new path) | `server.js:71-78` | Low (design note) |
| 7 | `get_db()` generator dependency and `transcribe_recording`'s manual `SessionLocal()` usage both correctly close sessions on every exception path — **no DB session leak found** | `database.py:25-33` | None — clean |
| 8 | `transcription_executor = ThreadPoolExecutor(max_workers=4)` has an unbounded internal queue — no back-pressure signal, and (per #2) exceptions raised outside the covered `try/except` vanish with no trace | `transcription_service.py:12` | Low (throughput/observability, pairs with #2) |
| 9 | `chat_service.ask_question`'s `if not response.candidates: raise ValueError(...)` isn't caught locally — the user's turn is already appended to session history before this point, so a raised exception leaves a dangling unanswered turn that corrupts the next request's context. `LRUSessionCache` (`chat_service.py:12-31`) can also evict a session mid-request under high concurrency (&gt;500 concurrent sessions) | `chat_service.py:147-148` | Low — chat/RAG only, not meeting-lifecycle |
| 10 | `index_transcript`'s delete-then-insert isn't atomic across its two commits (delete commits before insert) — a crash between them leaves zero indexed chunks until the next successful re-index (which `/retry` triggers automatically) | `embedding_service.py:187-211` | Low — self-heals on retry |
| — | `pdf_service.py`, `embedding_service.py` otherwise reviewed in full — no other meeting-lifecycle-relevant issues found | — | None |

---

## Severity summary

| Finding | Severity | Strands a meeting? |
|---|---|---|
| `notifyBackend` gives up after 3 retries, no persistence/watchdog | **Critical** | **Yes** |
| Unprotected tempfile write, swallowed by fire-and-forget executor | **High** | **Yes** |
| `bot.leave()`/`context.close()` has no timeout — also freezes the whole bot instance | **Medium** | **Yes** (lower probability, worse blast radius) |
| Ack-timeout race → status flip-back → possible duplicate transcription | **Medium** | No — data race |
| `fs.unlinkSync` failure mislabeled as upload failure | **Low** | No — self-heals via retry |
| Everything in the "lower-priority" table (#6–#10) | **Low / None** | No |

## Recommended next steps (not performed — report only)
1. **Add a backend watchdog/sweep** (e.g. a periodic task, or checked on next relevant request) that fails any meeting sitting in `joining`/`recording`/`uploading`/`transcribing` past a reasonable TTL, with an error message indicating a timeout — this single change neutralizes #1, #2, and #3 by giving every stuck meeting a path back to `"failed"`, where `/retry` can already recover it.
2. Attach a done-callback (or call `.result()` with exception handling) to every `transcription_executor.submit()` so a raised exception marks the meeting `"failed"` instead of vanishing.
3. Wrap `bot.leave()`/`context.close()` with an explicit timeout + fallback (e.g. `Promise.race` against a timer that force-kills the browser process), mirroring `FFmpegManager.stop()`'s escalation pattern.
4. Move `fs.unlinkSync(localPath)` (with its own try/catch) outside the upload's try block so cleanup failures don't mislabel a successful upload as failed.
5. Consider excluding `"failed"` from the webhook's `status NOT IN (...)` filter, or add an idempotency guard around `transcribe_recording` (e.g. a `transcribing_since`/lock column) to close the duplicate-transcription window in #5.
