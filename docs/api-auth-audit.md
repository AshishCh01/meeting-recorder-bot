# API Authentication &amp; Authorization Audit

Date: 2026-08-21
Scope: every FastAPI endpoint registered in `backend/app/main.py` (`backend/app/api/meetings.py`, `chat.py`, `webhooks.py`), plus the two auth dependencies in `backend/app/api/auth.py` that gate them.

Status: both Low findings below have since been fixed (2026-08-21) — see the "Fixed" notes on each.

---

## Endpoint inventory

There are 8 registered routes total (confirmed via `main.py:21-27` — no routers other than `meetings`, `webhooks`, `chat` are mounted).

| Method | Path | Auth dependency | Ownership check | Verdict |
|---|---|---|---|---|
| GET | `/health` | none | n/a | Correct — no sensitive data returned |
| POST | `/meetings` | `get_current_user` | n/a — creates a new row owned by the token's `user_id` | Correct |
| GET | `/meetings` | `get_current_user` | `Meeting.user_id == user_id` filter on the query itself | Correct |
| GET | `/meetings/{meeting_id}` | `get_current_user` | `Meeting.id == meeting_id, Meeting.user_id == user_id` | Correct |
| POST | `/meetings/{meeting_id}/retry` | `get_current_user` | `Meeting.id == meeting_id, Meeting.user_id == user_id` | Correct |
| GET | `/meetings/{meeting_id}/export-pdf` | `get_current_user` | `Meeting.id == meeting_id, Meeting.user_id == user_id` | Correct |
| POST | `/meetings/{meeting_id}/chat` | `get_current_user` | `Meeting.id == meeting_id, Meeting.user_id == user_id` | Correct |
| POST | `/webhooks/recording-complete` | `verify_webhook_token` (service-to-service) | filters by `meeting_id` only, no per-user check | See finding below — intentional design, one gap noted |

### How `user_id` is derived (`backend/app/api/auth.py:11-34`)
`get_current_user` takes the caller's bearer token and calls `supabase.auth.get_user(token)` — server-side verification against Supabase Auth, not a locally-decoded/trusted JWT. The returned `user_id` is what every meetings/chat endpoint filters on. **No endpoint reads `user_id` from the request body or query string and uses it for authorization** — every ownership filter across all 6 authenticated endpoints uses the value returned by this dependency, never client input.

**Verdict: no endpoint accepts a `user_id`, `meeting_id`, or similar identifier from the request and skips the ownership check.** Every meetings/chat route that takes a `meeting_id` path parameter combines it with the server-derived `user_id` in the same `WHERE`/`.filter()` clause — a `meeting_id` belonging to another user simply won't match the query (returns 404, not another user's data).

---

## `/webhooks/recording-complete` — bearer token validation

Request: `POST /webhooks/recording-complete`, gated at the route level via `dependencies=[Depends(verify_webhook_token)]` (`webhooks.py:13`) — this dependency runs **before** the handler body executes, so an invalid token never reaches the handler at all.

`verify_webhook_token` (`auth.py:38-44`):
```python
def verify_webhook_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    if not hmac.compare_digest(token, settings.meeting_bot_bearer_token):
        raise HTTPException(status_code=401, detail="Invalid webhook token")
```

**Confirmed: the token is genuinely validated, not just accepted.**
- It's compared against a real server-side secret (`settings.meeting_bot_bearer_token`, a required config field with no default — the app won't start without it set).
- The comparison uses `hmac.compare_digest`, a constant-time comparison — prevents timing-attack byte-by-byte token recovery.
- `HTTPBearer` (the `security` dependency) has `auto_error=True` by default, so a missing/malformed `Authorization` header is rejected before `verify_webhook_token` even runs.
- The same secret is used symmetrically in the other direction — `bot_service.py:27` sends it as the `Authorization` header when the backend calls `meeting-bot`'s join endpoints — confirming this is a real shared-secret trust boundary between the two services, not a stub.

**Severity: none — this control works as intended.**

### Finding: `payload.user_id` is accepted but never checked (Low)
`RecordingCompleteWebhook` (`backend/app/models/meeting.py:31-36`) has a `user_id` field, but the handler (`webhooks.py:14-70`) never reads it — it looks the meeting up by `payload.meeting_id` alone and never cross-checks that `payload.user_id` matches the meeting's actual `Meeting.user_id`.

This is **not exploitable by an external attacker** — the endpoint is only reachable with the shared bearer secret, `meeting_id` is an unguessable UUID, and the caller (`meeting-bot`) is a trusted internal service, not an end user. The risk is narrower: if `meeting-bot` itself ever sent a mismatched `meeting_id`/`user_id` pair (a bug, not an attack), this endpoint would silently update the wrong meeting with no cross-check to catch it. Since `user_id` isn't used for anything today, this is a dead/unvalidated field rather than a security control that's failing.

- **Severity: Low.** Recommend either using `payload.user_id` as a defense-in-depth cross-check (`Meeting.id == meeting_id, Meeting.user_id == payload.user_id`) or removing the unused field — as-is it looks like an authorization check that isn't actually happening.

**Fixed:** `webhooks.py` now compares `str(meeting.user_id) != payload.user_id` immediately after the meeting lookup and raises `409` on mismatch, before any update is applied.

---

## Secondary finding: CORS config bakes dev origins into production code (Low)

`backend/app/main.py:9-19`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_origin,
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
`http://localhost:5173` / `http://127.0.0.1:5173` are hardcoded into the CORS allow-list unconditionally — this is the same file that runs in production (no environment branch). Combined with `allow_credentials=True`, this means a page served from `localhost:5173` on the machine of anyone hitting the deployed API would be allowed to make credentialed cross-origin requests against production. In practice this requires the attacker to already control a process bound to that exact local port on the victim's machine, which is a narrow and unusual precondition — not a remotely exploitable hole — but it's sloppy: dev-only origins shouldn't ship in the same allow-list as production, and it's easy to forget to remove.

- **Severity: Low.** Recommend gating the two localhost origins behind an environment check (e.g. only added when a `DEBUG`/`ENV=development` setting is true) rather than always including them.

**Fixed:** `config.py` adds `environment: str = "production"` (defaults closed). `main.py` now only appends the two localhost origins to `cors_origins` when `settings.environment == "development"`; `settings.frontend_origin` is still always included. Local dev via `FRONTEND_ORIGIN=http://localhost:5173` (already set in `backend/.env`) is unaffected — set `ENVIRONMENT=development` there too if you also need the `127.0.0.1:5173` variant.

---

## Severity summary

| Finding | Severity | Exploitable by an external attacker today? | Status |
|---|---|---|---|
| Any endpoint accepting `user_id`/`meeting_id` without ownership check | **None found** | No — every authenticated route filters by server-derived `user_id` | n/a |
| `/webhooks/recording-complete` bearer token not actually validated | **None found** | No — genuinely validated with `hmac.compare_digest` against a required server secret | n/a |
| `payload.user_id` in webhook accepted but never cross-checked | **Low** | No — only reachable by the trusted internal `meeting-bot` service; risk is a silent data-integrity bug, not an auth bypass | **Fixed** |
| Dev CORS origins (`localhost:5173`) hardcoded into prod `main.py` | **Low** | No — requires attacker-controlled process on victim's own machine at that exact port | **Fixed** |
