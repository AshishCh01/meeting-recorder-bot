# Supabase RLS &amp; Backend Authorization Audit

Date: 2026-08-21
Scope: every Postgres table defined in the repo (`supabase/migrations/**`, `backend/alembic/versions/**`), their RLS status/policies, and whether the FastAPI backend's use of the Supabase service-role key is safe (i.e. independently ownership-checked before every service-role operation).

Status: report only. No fixes applied, no files modified.

---

## Part A — Table-by-table RLS status

A repo-wide search for `ROW LEVEL SECURITY`, `ENABLE ROW LEVEL SECURITY`, `CREATE POLICY`, and `auth.uid()` (case-insensitive, every file type) returned **zero matches anywhere in the repository.** No table has a tracked `ENABLE ROW LEVEL SECURITY` statement or a tracked `CREATE POLICY`. Everything below is "cannot verify from code" as a result — if RLS exists at all, it was configured by hand in the Supabase dashboard and is untracked, unreviewed, and could drift or be disabled without leaving any trace in this repo.

### `users`
- Defined: `backend/alembic/versions/475e9ee29ad9_initial_schema.py`
- RLS enable statement: not found
- Policies: none found
- Ownership column: `id` (PK, mirrors the Supabase Auth user id)
- **Verdict: cannot verify from code.**
- **Severity: Medium** — untracked/unverifiable RLS posture. Not currently exploitable (see Part B — nothing queries this table via a client that RLS would apply to), but there is no code-level guarantee it's configured correctly, or configured at all.

### `meetings`
- Defined: `supabase/migrations/create_meetings.sql`; altered in `475e9ee29ad9` (adds `user_id` + FK) and `8f2c1a9d4b6e_...` (adds `embedding_provider`)
- RLS enable statement: not found
- Policies: none found
- Ownership column: `user_id` (UUID, FK → `users.id`) — present, but **`nullable=True`** at both the SQLAlchemy model level (`backend/app/db/models.py:22`) and the migration level. A naive-but-correct-looking policy like `USING (user_id = auth.uid())` would silently make any row with a NULL `user_id` invisible to *everyone*, not protected from anyone — a correctness trap if RLS is ever added later without also fixing nullability.
- **Verdict: cannot verify from code.**
- **Severity: Medium** — same untracked-RLS reasoning as `users`, plus the nullable-FK correctness trap noted above (**Low** on its own, rolled into the Medium rating here).

### `meeting_chunks`
- Defined: **nowhere.** No `CREATE TABLE meeting_chunks` exists anywhere in the repo — it's only referenced via `ALTER TABLE`/`TRUNCATE` in `475e9ee29ad9:24-54`, meaning the table was created out-of-band (dashboard or a one-off script never committed). SQLAlchemy model exists at `backend/app/db/models.py:40-53`.
- RLS enable statement: not found
- Policies: none found
- Ownership column: **none directly** — only `meeting_id` (FK → `meetings.id`). Ownership is only derivable by joining to `meetings.user_id`. A correct RLS policy here needs a subquery/EXISTS against `meetings`, which is easy to get wrong and, again, nothing in the repo defines or tests it either way.
- **Verdict: cannot verify from code.**
- **Severity: High** — this table isn't just unverifiable, it's *undocumented*: there's no tracked schema for it at all, so there's no way to even confirm what columns/constraints exist in production, let alone whether RLS is correctly scoped through the `meetings` join.

### Cross-cutting: why this matters more than "just enable RLS in the dashboard"

**Severity: High (latent, conditional).** The Supabase anon key is public by design — it ships embedded in the built frontend JS bundle (confirmed: `frontend/src/lib/supabase.js` uses only the anon key). If RLS is **not** actually enabled on `meetings`/`meeting_chunks`/`users` in the live project (which cannot be confirmed from this codebase), then anyone with that public anon key — which is trivially extractable from the deployed frontend, not a secret — could query the Supabase REST API (PostgREST) **directly**, bypassing the FastAPI backend entirely, and read or write every user's meetings and transcript chunks. This is not a theoretical client-misuse scenario; it doesn't require the frontend to ever call `.from()` — an attacker can hit `https://<project>.supabase.co/rest/v1/meetings` from `curl` with just the public anon key.

Today, the frontend never calls Supabase's data API directly (confirmed below), so this isn't being *exercised* by the shipped app — but that's an application-layer accident, not a database-layer guarantee. Whether this is actually exploitable right now depends entirely on whether RLS is enabled in the live Supabase project, which is unverifiable from this repo. **Recommend confirming RLS status directly in the Supabase dashboard as a follow-up to this report.**

---

## Part B — Backend service-role usage and ownership verification

### Which key the backend uses
- `backend/app/config.py:6` loads `SUPABASE_KEY` from `backend/.env`; `backend/app/db/supabase.py:4` builds one module-level client with it, reused everywhere (`auth.py`, `meetings.py`, `webhooks.py`, `transcription_service.py`).
- Decoded the JWT's `role` claim only (raw token not reproduced): **`role: "service_role"`.** This key bypasses RLS unconditionally on every request.
- The direct SQLAlchemy connection (`backend/app/db/database.py:8-20`) also bypasses RLS: it connects as `postgres.<project-ref>` via Supabase's pooler — the table-owner role, which RLS doesn't restrict unless `FORCE ROW LEVEL SECURITY` is set (it isn't, anywhere in this repo).

**Finding — Severity: Medium.** Both DB access paths available to the backend bypass RLS entirely. This means RLS (even if perfectly configured) provides **zero defense-in-depth** for backend-originated queries — every authorization guarantee for this app currently rests entirely on the FastAPI code remembering to filter by `user_id` on every query. That code currently does this correctly everywhere it was checked (see below), but there is no database-level backstop if a future endpoint forgets to.

### Service-role operations and their ownership checks

| Operation | File:Line | Ownership check before it? | Verified at |
|---|---|---|---|
| Signed URL generation (list endpoint) | `backend/app/api/meetings.py:79` | **Yes** | `meetings.py:71` — query pre-filtered by `Meeting.user_id == user_id` |
| Signed URL generation (single-meeting endpoint) | `backend/app/api/meetings.py:101` | **Yes** | `meetings.py:93` — `Meeting.id == meeting_id, Meeting.user_id == user_id` |
| List storage files (retry flow) | `backend/app/api/meetings.py:134` | **Yes** | `meetings.py:116` — same pattern |
| Download recording bytes for transcription | `backend/app/services/transcription_service.py:181-183` | **Yes**, at the trigger site | called only from `webhooks.py:55-58` (HMAC-verified service-to-service bearer, `auth.py:38-44`) or `meetings.py:146-150` (post-ownership-check retry) |
| RAG tool calls: `get_meeting_summary`, `get_action_items`, `search_by_speaker`, `search_transcript` | `backend/app/rag/tools.py:30,57,81,140` | **Yes, independently** | each tool re-queries `Meeting.id == meeting_id, Meeting.user_id == user_id` itself, rather than trusting the entry-point check |
| Chat entry point | `backend/app/api/chat.py:18` | **Yes** | ownership checked before invoking the agent |

**No endpoint was found where a service-role call executes using a client-controllable identifier without a prior `user_id`-scoped SQLAlchemy filter gating it.** Storage paths (`f"{user_id}/{meeting_id}/..."`) are always built from a `Meeting` row already fetched under a `user_id ==` filter — the `user_id` in the path comes from the trusted DB row, not from client input.

**Finding — Severity: none (informational, verified clean).** Every service-role operation checked has a correct, independent ownership check. The RAG tool layer in particular re-verifies ownership per-tool-call rather than relying on the chat entry point alone, which is good defense-in-depth practice at the application layer (even though it doesn't compensate for the DB-layer gap noted above).

### How "current user" is derived
`backend/app/api/auth.py:11-34` (`get_current_user`) takes the bearer token, calls `supabase.auth.get_user(token)` (`auth.py:17`) — server-side JWT verification against Supabase Auth. Every `user_id` used in ownership checks throughout the backend comes from this dependency's return value, **never from a client-supplied body/query `user_id`.**

One place a client-labeled `user_id` does appear in a request body: `RecordingCompleteWebhook.user_id` (`backend/app/models/meeting.py:32`). That endpoint (`webhooks.py:13-26`) is gated by `verify_webhook_token` (a shared secret between the backend and `meeting-bot`, not end-user auth), and the handler doesn't even read `payload.user_id` for anything authorization-relevant — it looks the meeting up by `payload.meeting_id` alone. This is intentional service-to-service auth, not end-user impersonation, and is not a finding.

**Finding — Severity: none (informational, verified clean).** No endpoint trusts a client-supplied `user_id` in place of the verified-token identity for authorization decisions.

### Supplementary context
- `frontend/src/lib/supabase.js` uses the Supabase client **only** for `signInWithPassword`/`signUp`/`getSession`/`onAuthStateChange` — confirmed via a full grep of `supabase.` calls in `frontend/src`. It never calls `.from()` or `.storage` directly; all data/storage access is proxied through the FastAPI backend's own ownership-checked endpoints. This is why the Part A RLS gap has no exploitable surface *through the shipped frontend* today — but as noted above, that's an application choice, not something the anon key's public nature prevents an external actor from bypassing directly.
- `meeting-bot/src/storage/SupabaseUploader.js:9-13` also uses a service-role key (comment confirms it), but is only reachable via a bearer-token-protected route (`meeting-bot/src/api/server.js:30-43,73-75`) called by the backend with its own already-verified `user_id` — not user-facing, not a finding.

---

## Severity summary

| Finding | Severity | Exploitable today? |
|---|---|---|
| `meeting_chunks` has no tracked schema anywhere in the repo | **High** | N/A — process/auditability gap, not itself an access-control bug |
| RLS status for all 3 tables is untracked/unverifiable from code | **High (latent)** | Unknown — depends on live Supabase dashboard config, which this audit cannot see. If RLS is off, the public anon key allows direct cross-user read/write via PostgREST, bypassing the backend entirely |
| Both backend DB access paths (service-role Supabase client, DB-owner Postgres connection) bypass RLS unconditionally, so RLS is not a backstop for backend code | **Medium** | Not currently — app-layer checks are correct everywhere audited — but a single future endpoint that forgets a `user_id` filter would have no DB-level safety net |
| `meetings.user_id` is nullable, which would break a naive RLS policy's intent if RLS is added later | **Low** | No |
| Backend service-role operations lack ownership checks | **None found** | No cross-user read/write path identified in any endpoint or service audited, including the RAG tool-calling layer |
| Client-supplied `user_id` trusted over verified auth identity | **None found** | No endpoint does this |

## Recommended next steps (not performed — report only)
1. Confirm directly in the Supabase dashboard whether RLS is actually enabled on `users`, `meetings`, and `meeting_chunks` — this repo cannot answer that question, and it's the single highest-value unknown from this audit.
2. If RLS is off (or you can't be sure), enable it with correct `auth.uid() = user_id` policies for `meetings`/`users`, and an `EXISTS`-against-`meetings` policy for `meeting_chunks`, even though the backend doesn't currently rely on it — treat it as defense-in-depth against future code paths (e.g. a client-side Supabase call added later, or a bug in a future endpoint).
3. Add a tracked migration for `meeting_chunks` (`CREATE TABLE` doesn't exist anywhere) so the schema is reviewable and reproducible.
4. Consider making `meetings.user_id` `NOT NULL` if every meeting is expected to have an owner, to avoid the NULL-row RLS trap noted above.
