# Google Calendar integration — opt-in scheduled recording

## Context

Today a meeting can only be recorded by pasting its URL into the Dashboard, which triggers the bot immediately (`POST /meetings` in [backend/app/api/meetings.py](../backend/app/api/meetings.py) calls `trigger_bot_join` inline). There's no concept of a future-dated join, and no calendar awareness — `frontend/src/pages/Settings.jsx` already has a static "Connected calendar" card with a Google Calendar row marked **Coming soon**, which is the scaffold this plan wires up.

Goal: let a user connect their Google Calendar, see their upcoming events with a detected Meet/Zoom link, and opt a specific event into recording — the bot then joins automatically a couple of minutes before that event starts. No auto-record-everything behavior (confirmed with the user — opt-in per event only).

This requires two genuinely new capabilities the app doesn't have today: Google OAuth (separate from Supabase login) and a real "join in the future" scheduler (today's `status: scheduled` is just a transient pre-`joining` state, not a timer).

## Data model changes

**New table `calendar_connections`** (one row per user, Alembic migration following the shape of [backend/alembic/versions/b2c3d4e5f6a7_add_bot_display_name_to_users.py](../backend/alembic/versions/b2c3d4e5f6a7_add_bot_display_name_to_users.py)):
- `id` UUID PK, `user_id` UUID FK → `users.id` ON DELETE CASCADE, unique
- `google_email` String
- `refresh_token_encrypted` String — Fernet-encrypted, never stored in plaintext
- `created_at`, `updated_at`

Add matching RLS policy mirroring the existing `meetings` policy from [backend/alembic/versions/e6a4d8f0b2c1_enable_rls_and_policies.py](../backend/alembic/versions/e6a4d8f0b2c1_enable_rls_and_policies.py) (owner-only access) — backend connects via the service-role/pooler connection so this is defense-in-depth, not load-bearing, but keeps parity with the rest of the schema.

**Extend `meetings`** (second migration): add `scheduled_at TIMESTAMPTZ NULL` and `calendar_event_id VARCHAR NULL`, plus a partial unique index on `(user_id, calendar_event_id) WHERE calendar_event_id IS NOT NULL` so the same calendar event can't be scheduled twice. Manual immediate meetings leave `scheduled_at` null and behave exactly as they do today — no existing code path changes behavior.

No changes needed to `supabase/migrations/create_meetings.sql` (the one manually-run bootstrap file) — per the README, everything else, `calendar_connections` included, is Alembic-managed and created automatically on backend startup.

Update `backend/app/db/models.py`: add a `CalendarConnection` model, add the two new columns to `Meeting`.

## OAuth flow

New `backend/app/services/google_oauth_service.py`:
- `build_auth_url(state)` — Google's `/o/oauth2/v2/auth` with `scope=calendar.readonly`, `access_type=offline`, `prompt=consent` (forces a refresh token even on reconnect).
- `exchange_code_for_tokens(code)` — POST to Google's token endpoint.
- `encrypt_token`/`decrypt_token` — `cryptography.fernet.Fernet` keyed by a new required setting `google_token_encryption_key` (generated once via `Fernet.generate_key()`, same treatment as the other secrets in `backend/.env.example`).
- `state` param is a signed (HMAC via the same Fernet key), self-verifying blob encoding `user_id` + expiry — no extra "pending state" table needed, and the callback (hit directly by Google's redirect, no Authorization header available) can still recover which user completed consent.

New `backend/app/services/calendar_service.py`:
- `get_valid_access_token(user_id, db)` — loads the connection row, decrypts the refresh token, exchanges it for a short-lived access token on demand (not persisted). Raises a distinct error if Google rejects the refresh token (revoked access) so the API layer can tell the frontend to prompt reconnecting.
- `list_upcoming_events(access_token, days_ahead)` — `GET .../calendars/primary/events` with `timeMin=now`, `timeMax=now+days_ahead`, `singleEvents=true` (this also cleanly solves recurring events — each occurrence gets its own stable Google event id, so per-occurrence opt-in "just works").
- `get_event(access_token, event_id) -> dict | None` — `GET .../calendars/primary/events/{event_id}` for a single event, raising distinguishable exceptions for "not found/cancelled" (404, or `status == "cancelled"` in the body — Google marks a deleted event this way rather than always 404ing) vs. "auth failure" (401/403 — revoked token) vs. transient network/timeout errors. Used both by `POST /calendar/events/{event_id}/schedule` and by the scheduler's pre-join revalidation below — one fetch-and-classify path, not two.
- `extract_meeting_url(event) -> (platform, url) | None` — checks `hangoutLink`, then `conferenceData.entryPoints`, then scans `location`/`description` for a URL, running each candidate through the **existing** `detect_platform()` in [backend/app/services/platform_detector.py](../backend/app/services/platform_detector.py). Reusing it means Teams links keep getting excluded exactly like manual submission does today — one allowlist, not two.

## New API endpoints

New router `backend/app/api/calendar.py`, registered in [backend/app/main.py](../backend/app/main.py) next to the existing routers:

- `GET /calendar/connect` (auth) → `{auth_url}`
- `GET /calendar/oauth/callback?code&state` (public — Google hits this directly) → verifies state, stores the encrypted connection, redirects to `{FRONTEND_ORIGIN}/settings?calendar=connected`
- `GET /calendar/status` (auth) → `{connected, google_email}`
- `DELETE /calendar/disconnect` (auth) → deletes the connection row
- `GET /calendar/events` (auth) → upcoming events with `id, title, starts_at, ends_at, meeting_url, platform, already_scheduled, meeting_id`
- `POST /calendar/events/{event_id}/schedule` (auth) → re-fetches that single event from Google server-side (never trusts a client-supplied URL/time), creates a `Meeting` row: `status="scheduled"`, `scheduled_at=event.start`, `calendar_event_id=event_id`, `title=event.summary` — same construction as `create_meeting` in [backend/app/api/meetings.py](../backend/app/api/meetings.py) minus the immediate `trigger_bot_join` call.

Unscheduling reuses the **existing** `DELETE /meetings/{meeting_id}` endpoint ([backend/app/api/meetings.py:172](../backend/app/api/meetings.py#L172)) — the `/calendar/events` response already carries `meeting_id` for events the user opted into, so the frontend just calls the delete route it already has.

New Pydantic schemas in a new `backend/app/models/calendar.py` (mirrors the existing `backend/app/models/meeting.py` pattern).

## Scheduler — the missing "join in the future" piece

Reuse the exact background-loop pattern already in [backend/app/main.py](../backend/app/main.py) (`_watchdog_loop` / `sweep_stale_meetings` in [backend/app/services/watchdog.py](../backend/app/services/watchdog.py)) rather than adding a new container or job runner:

New `backend/app/services/scheduler.py`, `trigger_due_meetings(db)`:
- Selects `Meeting` rows where `status == "scheduled"` and `scheduled_at` has arrived (within `calendar_join_lead_minutes` of now).
- For each: same call sequence as `create_meeting` today — set `status="joining"`, call `trigger_bot_join(...)`, mark `failed` on exception.
- Rows whose `scheduled_at` is more than ~15 minutes in the past (app was down, or otherwise missed) get marked `failed` with an explanatory `error_message` instead of joining a meeting that's already over.

**Revalidate calendar-sourced meetings before joining.** A meeting scheduled from a calendar event (`calendar_event_id IS NOT NULL`) can drift out of sync between the moment the user opted in and the moment it's due — the organizer might cancel it, move it, or swap the video link. Manually-created meetings (`calendar_event_id IS NULL`) skip this step entirely and join exactly as they do today. For a calendar-sourced meeting that's about to fire, `trigger_due_meetings` calls `calendar_service.get_event()` first and branches on the result:

- **Cancelled or deleted** (404, or `status == "cancelled"`) → mark `failed`, `error_message`: `"Calendar event was cancelled before the bot could join."` — don't call `trigger_bot_join`.
- **Start time moved later**, and the new start is still in the future → update `scheduled_at` to the new start, leave `status="scheduled"`, don't join this cycle. The next sweep re-evaluates it once the new time arrives (which re-runs this same revalidation, so a second reschedule is caught too).
- **Start time moved earlier**, and the new start is already more than the ~15-minute grace window in the past → mark `failed`, `error_message`: `"Meeting was rescheduled earlier and has already ended."`
- **Meeting URL or platform changed** (re-run `extract_meeting_url()` against the fresh event) → update `meeting_url`/`platform` on the row before joining, so the bot goes to the current link, not the one captured at opt-in time.
- **Still valid and on time** → proceed to `trigger_bot_join` as normal.
- **Re-fetch itself fails** → never silently skip the join *or* silently join on faith without a record of why:
  - Transient error (network/timeout, or any 5xx from Google) → proceed with the stored `meeting_url`/`scheduled_at` as a best-effort join, but log a warning naming the meeting and the error, e.g. `"[scheduler] Could not verify meeting <id> before joining (network error: ...) - proceeding with last-known details."` A momentary Google API blip shouldn't cost the user their recording.
  - Auth error (401/403 — the stored refresh token was revoked) → mark `failed`, `error_message`: `"Could not verify meeting details before joining — Google Calendar access was revoked. Reconnect your calendar."` Once the token itself is untrustworthy there's no "last known good" data worth acting on, and joining blind here risks recording the wrong meeting entirely.

Wire a second `asyncio.create_task` into `lifespan()` in `main.py`, identical shape to the existing watchdog task, gated by a new `calendar_scheduler_enabled` setting and a short `scheduler_sweep_interval_minutes` (default 1, since this is time-sensitive unlike the 5-minute watchdog sweep).

## Config additions ([backend/app/config.py](../backend/app/config.py))

`google_client_id`, `google_client_secret`, `google_oauth_redirect_uri`, `google_token_encryption_key` (required, no default), `calendar_lookahead_days` (default 7), `calendar_scheduler_enabled` (default true), `scheduler_sweep_interval_minutes` (default 1), `calendar_join_lead_minutes` (default 2). Document all of these in `backend/.env.example` and the README's env var table, same as every existing setting.

New dependency: `cryptography` in `backend/requirements.txt` (for Fernet). `httpx` is already a dependency and covers the Google token/API HTTP calls.

## Frontend changes

- [frontend/src/pages/Settings.jsx](../frontend/src/pages/Settings.jsx) lines 150-180: replace the static "Coming soon" Google Calendar row with a live version driven by `GET /calendar/status` — "Connect" redirects to the `auth_url` from `GET /calendar/connect`; once connected, show `google_email` and a "Disconnect" button wired to `DELETE /calendar/disconnect`. Handle the `?calendar=connected` redirect param for a success toast. The Outlook row and the Notifications card stay untouched/coming-soon — out of scope.
- New `frontend/src/components/dashboard/UpcomingEvents.jsx`, surfaced in [frontend/src/pages/Dashboard.jsx](../frontend/src/pages/Dashboard.jsx) (only fetched/shown once `/calendar/status` says connected): lists `GET /calendar/events`, each row showing title/time/platform and a toggle — "Record" calls `POST /calendar/events/{id}/schedule`, an already-scheduled event calls the existing `DELETE /meetings/{meeting_id}` to cancel. Events with no detected link render greyed/non-actionable. Reuse the visual language of the existing `frontend/src/components/dashboard/MeetingRow.jsx` / `EmptyState.jsx` rather than inventing new patterns.
- No changes needed in `frontend/src/lib/status.js` or the Dashboard status filters — `scheduled` already maps to the `muted` tone and the existing "Scheduled" filter ([frontend/src/pages/Dashboard.jsx:17](../frontend/src/pages/Dashboard.jsx#L17)) will naturally start showing real future-dated meetings once `scheduled_at` is populated, through the existing `GET /meetings` list.
- `frontend/src/lib/api.js`'s shared authed axios instance is reused as-is for every new call — no new HTTP client needed.

## Verification

1. `docker compose up -d --build`; confirm both new Alembic migrations apply in `docker compose logs backend` (`alembic upgrade head`, same as the README's "healthy backend log" check).
2. Create a Google Cloud OAuth client (Web application), add `http://localhost:8000/calendar/oauth/callback` as an authorized redirect URI, generate an encryption key, and set `google_client_id` / `google_client_secret` / `google_oauth_redirect_uri` / `google_token_encryption_key` in `backend/.env`.
3. Log in → Settings → Connect Google Calendar → complete consent → confirm redirect back shows "connected" and a row exists in `calendar_connections`.
4. Dashboard → confirm upcoming events with real Meet/Zoom links appear; toggle "Record" on one starting a few minutes out; confirm a `meetings` row appears with `status=scheduled`, `scheduled_at` set, `calendar_event_id` set.
5. Wait for `scheduled_at` minus the lead time; confirm backend logs show the scheduler loop firing `trigger_bot_join`, meeting-bot logs show it joining, and status flows `scheduled → joining → ...` exactly like a manual meeting does today.
6. Cancel a scheduled-but-not-yet-fired meeting via the toggle; confirm it's gone from both the Dashboard and the Upcoming list.
7. Disconnect the calendar; confirm the `calendar_connections` row is removed and `/calendar/events` now returns a clear "not connected" error instead of a stack trace.
8. Revalidation, exercised directly against a scheduled row rather than waiting on real calendar timing: opt into an event, then in Google Calendar (a) delete it → confirm the next sweep marks the meeting `failed` with the cancellation message and never calls `trigger_bot_join`; (b) push its start time later → confirm `scheduled_at` updates and the meeting stays `scheduled` until the new time; (c) swap its Meet link for a different one → confirm the bot joins the new link, not the original. Separately, temporarily revoke the app's access from the Google account's permissions page and confirm a due meeting fails with the "Google Calendar access was revoked" message rather than joining.
