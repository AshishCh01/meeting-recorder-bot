# Render Deployment Checklist

Date: 2026-08-21
Target topology: `frontend` as a Render **Static Site**, `backend` as a Render **Docker Web Service**, `meeting-bot` as a Render **Docker Private Service**.

Status: report only. No code changed.

---

## 1. Environment variables per service

### backend (Docker Web Service) — all runtime env vars, none are build args

| Var | Required? | Production value | Runtime/build |
|---|---|---|---|
| `DATABASE_URL` | **Required** — app raises `ValueError` at import if missing (`database.py:8-10`) | secret — Supabase Postgres connection string (use the pooler URL, `postgresql://...`) | runtime |
| `SUPABASE_URL` | **Required** — pydantic `ValidationError` at startup if missing | secret — your Supabase project URL | runtime |
| `SUPABASE_KEY` | **Required** | secret — service-role key | runtime |
| `MEETING_BOT_BEARER_TOKEN` | **Required** | secret — generate a strong random value. **Must exactly match `BEARER_TOKEN` on meeting-bot** — it's one shared secret used in both directions | runtime |
| `GEMINI_API_KEY` | **Required** | secret | runtime |
| `GOOGLE_API_KEY` | Not read by any app code path that matters | **do not set** — an unused `google.adk` import (`app/rag/agent.py`) forwards `GEMINI_API_KEY` into this automatically at startup; setting it separately just risks it drifting out of sync (see `docs/deploy-readiness.md` appendix) | n/a |
| `SUPABASE_RECORDINGS_BUCKET` | Optional, default `"recordings"` | leave default unless your bucket is named differently | runtime |
| **`MEETING_BOT_URL`** | Optional but **must be overridden** | ⚠️ **Differs from local.** Local default/`.env` value is `http://meeting-bot:3000` (docker-compose DNS) — on Render this must be meeting-bot's Render-assigned URL (see section 5 for how private-service addressing works). If left unset, falls back to `http://localhost:3000` and silently fails with connection-refused, not an obvious error. | runtime |
| **`FRONTEND_ORIGIN`** | Optional but **must be overridden** | ⚠️ **Differs from local.** Default is `http://localhost:5173` — on Render must be the frontend Static Site's URL (`https://<your-frontend>.onrender.com`, or your custom domain). If forgotten, CORS silently rejects every request from your real frontend with no server-side error pointing at the cause. | runtime |
| `ENVIRONMENT` | Optional, default `"production"` | leave unset (or explicitly `production`) — do **not** set to `"development"`, that reopens the two hardcoded localhost CORS origins | runtime |
| `GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `RAG_AGENT_MODEL`, `CHUNK_SEGMENTS`, `CHUNK_OVERLAP`, `RETRIEVAL_TOP_K` | Optional | defaults are fine | runtime |
| `SARVAM_API_KEY` | Optional, default `""` (disables the fallback path cleanly) | secret if you want the Sarvam STT fallback enabled | runtime |
| `SARVAM_STT_MODEL`, `SARVAM_CHAT_MODEL`, `SARVAM_LANGUAGE_CODE`, `SARVAM_NUM_SPEAKERS` | Optional | defaults fine | runtime |
| `JINA_API_KEY` | Optional, default `""` | secret if you want the Jina embedding fallback enabled | runtime |
| `JINA_EMBEDDING_MODEL` | Optional | default fine | runtime |
| `WATCHDOG_ENABLED`, `WATCHDOG_SWEEP_INTERVAL_MINUTES`, `WATCHDOG_JOINING_TTL_MINUTES`, `WATCHDOG_RECORDING_MARGIN_MINUTES`, `WATCHDOG_UPLOADING_TTL_MINUTES`, `WATCHDOG_TRANSCRIBING_TTL_MINUTES` | Optional | defaults fine | runtime |
| `MAX_RECORDING_DURATION_MINUTES` | Optional, default `90` | **Keep this in sync with the same var on meeting-bot** — backend's watchdog uses it to compute the "recording" TTL (this value + margin); meeting-bot uses its own copy as the bot's actual hard join-duration cap. If you override one, override both to the same number. | runtime |

### meeting-bot (Docker Private Service) — all runtime env vars

| Var | Required? | Production value | Runtime/build |
|---|---|---|---|
| `BEARER_TOKEN` | **Required** — the process now exits at startup if unset (fixed this session; previously this was silently exploitable, see `docs/deploy-readiness.md`) | secret — **same value as backend's `MEETING_BOT_BEARER_TOKEN`** | runtime |
| `BACKEND_WEBHOOK_URL` | **Required** — process exits at startup if unset | ⚠️ **Differs from local.** Local `.env` value is `http://backend:8000/webhooks/recording-complete` — on Render, `https://<your-backend>.onrender.com/webhooks/recording-complete` | runtime |
| `SUPABASE_URL` | **Required** — process exits at startup if unset | secret — **same Supabase project as backend** | runtime |
| `SUPABASE_KEY` | **Required** — process exits at startup if unset | secret — service-role key, same as backend's | runtime |
| `AUTH_STATE_PATH` | Optional (falls back to a path that won't exist on Render) but **effectively required** | `/etc/secrets/auth.json` — see section 2 | runtime |
| `PORT` | Optional, default `3000` | leave unset unless Render requires you to set it explicitly for this service type — code already reads `process.env.PORT \|\| 3000` | runtime |
| `SUPABASE_RECORDINGS_BUCKET` | Optional, default `'recordings'` | leave default, must match backend's bucket | runtime |
| `MAX_RECORDING_DURATION_MINUTES` | Optional, default `90` | keep in sync with backend's copy (see above) | runtime |

Note: `BEARER_TOKEN` and `BACKEND_WEBHOOK_URL`/`SUPABASE_URL`/`SUPABASE_KEY` were **not** validated at meeting-bot startup until this session's fixes — a missing value used to either silently disable auth entirely (`BEARER_TOKEN`) or fail deep inside a retry loop with only a log line (`BACKEND_WEBHOOK_URL`). As of the current code, all four are checked at process start and the container will refuse to come up (exit 1) if any are missing — which is what you want on Render: a crash-looping service with a clear log line is much easier to debug than one that starts fine and silently misbehaves.

### frontend (Static Site) — build-time only, not runtime

Render Static Sites have no runtime process — these three must be set as **build environment variables** in the Static Site's settings, so they're present when Render runs your build command and Vite bakes them into the bundle. Setting them anywhere else (or only at "runtime," which doesn't exist for a static site) does nothing.

| Var | Required? | Production value |
|---|---|---|
| `VITE_API_URL` | **Required in practice** — as of this session's fix, the app now throws at module load if missing, so a forgotten value fails the build/breaks the page loudly instead of silently routing requests at the frontend's own origin | `https://<your-backend>.onrender.com` (no trailing slash) |
| `VITE_SUPABASE_URL` | Required | your Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Required | Supabase anon key (public-safe by design, fine to expose client-side) |

---

## 2. `auth.json`

**`AUTH_STATE_PATH` wiring — confirmed correct.** `meeting-bot/src/core/BrowserManager.js` resolves it as `process.env.AUTH_STATE_PATH || DEFAULT_AUTH_STATE_PATH` (the compose bind-mount path), and `meeting-bot/src/index.js` calls `assertAuthStateExists()` at process startup — before the server starts listening — exiting with a clear error naming the resolved path if the file isn't there.

**What to set it to:** upload your local `auth.json` as a Render **Secret File** on the meeting-bot service. Render mounts secret files at `/etc/secrets/<filename>` by default, so if you upload it as `auth.json`:
```
AUTH_STATE_PATH=/etc/secrets/auth.json
```

**Critical ordering note:** because the startup check now runs *before* `app.listen()`, **the Secret File must be attached before meeting-bot's first deploy/start** — if you create the service and let it deploy before uploading the file, it will crash-loop on every boot with the `AUTH_STATE_PATH resolved to "..." but no file exists there` error until you add it and redeploy. Attach the secret file first, then trigger the first deploy.

**Is `auth.json` excluded from git and Docker layers?**
- **Docker: yes, confirmed clean.** `meeting-bot/.dockerignore` excludes both `meeting-bot/auth.json` and (added this session) `generate-auth.cjs`. No image layer has ever contained it.
- **Git: no — this is still an open issue, not something this deploy checklist can wave through.** `.gitignore` lists `auth.json` and `meeting-bot/auth.json`, but the file was committed once (`07294a1`, "meet and zoom fixed") *before* those ignore rules existed, and git continues tracking a file once it's been committed regardless of later `.gitignore` entries. Per `docs/secrets-audit.md`, this file — a live Google account session — is still tracked in git and already pushed to `origin/main`. **Recommend resolving this (rotate the Google account credential, `git rm --cached`, purge from history, force-push) before or shortly after this deploy**, independent of the Render setup itself — it doesn't block deploying, but it's an active, unresolved credential exposure sitting on your remote right now.

---

## 3. Build config per service

### backend — Docker Web Service
- **Dockerfile:** `backend/Dockerfile` (the production one — **not** `backend/Dockerfile.dev`, which is compose-only and runs `uvicorn --reload`)
- **Root directory:** `backend`
- **Port:** hardcoded — `EXPOSE 8000` and the `CMD` hardcodes `--port 8000`; the app does **not** read a `PORT` env var. This should work automatically since Render detects the Dockerfile's `EXPOSE` instruction for Docker-based services, but **explicitly set the service's port to `8000`** in Render's dashboard rather than relying purely on auto-detection, as a safety net.
- **Health check path:** `GET /health` is available — set it as the health check path if Render's UI offers one for this service type, so Render can tell a genuinely broken deploy from a slow-starting one.
- **Instance count:** keep at **1**. The `CMD` already hardcodes `--workers 1` deliberately — `chat_service.py`'s session cache and `transcription_service.py`'s `ThreadPoolExecutor` both live in process memory, so multiple *instances* (not just multiple uvicorn workers) would each have their own copy, and a user's chat session or a transcription task could inconsistently route to a different instance. Don't horizontally scale this service without first addressing that.
- **Startup command:** `sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"` (already in the Dockerfile's `CMD` — migrations run automatically on every boot, idempotently).

### meeting-bot — Docker Private Service
- **Dockerfile:** `meeting-bot/Dockerfile` (the only one — no `.dev` variant exists)
- **Root directory:** `meeting-bot`
- **Port:** `EXPOSE 3000`, and the code *does* read `process.env.PORT || 3000` (`src/index.js:5`) — this one is Render-injection-friendly, no action needed beyond confirming Render's assigned port matches what you expect.
- **Health check path:** `GET /health` available.
- **Instance count:** keep at **1** — `server.js`'s `activeMeeting` module-level singleton guard (tied to a global `AudioRouter` audio-device singleton) means only one meeting can be in progress system-wide per running instance; more than one instance would let two meetings run concurrently and silently corrupt each other's audio routing.
- **Secret File:** `auth.json`, mounted at `/etc/secrets/auth.json` — must be attached before first deploy (section 2).
- **Shared memory:** docker-compose sets `shm_size: '2gb'` for this service (Chromium needs it for video/audio processing). Render's Docker services have a fixed `/dev/shm` size tied to your instance plan — verify your chosen plan gives Chromium enough headroom, or add `--disable-dev-shm-usage` handling if you see Chromium crashes (the Playwright launch args in `BrowserManager.js` already include `--disable-dev-shm-usage`, which mitigates this by having Chromium use `/tmp` instead of `/dev/shm` — likely fine on most plans, but worth watching in logs after the first real meeting).

### frontend — Static Site (Docker is not used at all here)
- **`frontend/Dockerfile` and `frontend/Dockerfile.dev` are both irrelevant** for a Static Site deploy — Render's Static Site product doesn't invoke Docker; it runs your build command directly in a Node build environment and serves the output as static files.
- **Root directory:** `frontend`
- **Build command:** `npm ci && npm run build` (or `npm install && npm run build`)
- **Publish directory:** `dist` (Vite's default output)
- **SPA routing:** the nginx config baked into `frontend/Dockerfile` (the `try_files $uri $uri/ /index.html` fallback for client-side routing) **does not apply** to a Static Site deploy — nginx is never used. You must configure Render's own rewrite rule instead: a catch-all redirect/rewrite `/*` → `/index.html` (Rewrite, not Redirect) in the Static Site's settings, or every client-side route (e.g. `/dashboard`) will 404 on a direct load or page refresh.

---

## 4. Anything that will break (local-only assumptions)

1. **`MEETING_BOT_URL` / `BACKEND_WEBHOOK_URL` / `FRONTEND_ORIGIN`** all currently point at docker-compose service-name DNS or `localhost` (`backend/.env`: `MEETING_BOT_URL=http://meeting-bot:3000`; `meeting-bot/.env`: `BACKEND_WEBHOOK_URL=http://backend:8000/...`; both backend config defaults point at `localhost`). None of these resolve on Render. Covered above in section 1 — this is the single most important thing to get right, since a wrong value in either direction fails silently or with a confusing generic error, not an obvious "wrong URL" message.
2. **`VITE_API_URL`/`VITE_SUPABASE_*`** must be set as Render *build* environment variables for the Static Site specifically — not runtime env vars anywhere else, since a Static Site has no runtime process. Setting them only on the backend or forgetting the Static Site's own build-env settings will silently bake `undefined` into the bundle (mitigated this session — it now throws at build/module-load instead of silently misrouting, but the build will fail loudly rather than the more helpful "you forgot to set X here").
3. **`auth.json` bind-mount assumption** — already fixed this session (`AUTH_STATE_PATH`), now needs the Render Secret File wiring in section 2. Ordering matters (must exist before first start).
4. **Ephemeral disk on both Docker services.** Neither backend nor meeting-bot have a persistent disk on Render by default. This is fine for backend (no runtime-written local storage at all — everything goes through Supabase). For meeting-bot: the local `recordings/` file is transient scratch space by design (deleted after a successful upload) — fine. The one behavior change: on an **upload failure**, the code deliberately keeps the local file for debugging; under compose that persists via a host bind mount, but on Render it's lost on the next restart/redeploy. Not a functional bug (the failure is still correctly reported and retryable), just a lost debugging artifact for that specific failure path.
5. **Backend `--workers 1` / meeting-bot's single-active-meeting guard** — both require exactly one running instance each (see section 3). Don't change Render's instance-count/scaling settings for either without first addressing the underlying in-memory-state assumptions.
6. **The backend's watchdog sweep** (added this session, runs as an in-process `asyncio` loop tied to the FastAPI process) only runs while the instance is up. If you're on a Render plan/tier that spins the service down on idle, the watchdog simply pauses until the next request wakes it — not a bug, just something to know if you're relying on it to catch stuck meetings promptly.
7. **`GOOGLE_API_KEY`** — not a breakage risk, just noise: don't set it. See section 1.

Nothing else assumes a bind mount, a persistent disk, or compose-specific networking beyond what's listed above — confirmed via the full env-var and local-assumption sweep in `docs/deploy-readiness.md`, which this checklist builds on.

---

## 5. Deploy order

Circular dependency: backend needs meeting-bot's URL and frontend's URL; meeting-bot needs backend's URL; frontend needs backend's URL. Render assigns a service's URL as soon as you create it (before the first build necessarily finishes), so you don't need everything live simultaneously — just create services in an order that lets you fill in each URL as soon as it exists, then circle back and patch the two backend env vars that depend on services created after it.

1. **Prerequisites (not Render steps):** Supabase project already has its schema/RLS applied and you have `DATABASE_URL`/`SUPABASE_URL`/`SUPABASE_KEY` in hand; you've run `generate-auth.cjs` locally and have a fresh `auth.json` ready to upload; you've picked a `MEETING_BOT_BEARER_TOKEN` value (used as both `MEETING_BOT_BEARER_TOKEN` and `BEARER_TOKEN`).
2. **Create the backend Docker Web Service.** Set every required var from section 1 except `MEETING_BOT_URL` and `FRONTEND_ORIGIN` (leave defaults for now — the app still starts fine, it just can't reach meeting-bot or accept frontend CORS yet). Deploy. Note its public URL.
3. **Create the meeting-bot Docker Private Service.** Attach the `auth.json` Secret File *first*. Set `BACKEND_WEBHOOK_URL` to the backend URL from step 2, `BEARER_TOKEN` matching backend's `MEETING_BOT_BEARER_TOKEN`, `SUPABASE_URL`/`SUPABASE_KEY`, `AUTH_STATE_PATH=/etc/secrets/auth.json`. Deploy. Note its Render-internal address (Render's dashboard shows the private-service hostname/URL to use for service-to-service calls within your project).
4. **Go back to backend and set `MEETING_BOT_URL`** to meeting-bot's address from step 3. Render redeploys automatically on env var change (or trigger it manually).
5. **Create the frontend Static Site.** Set `VITE_API_URL` to backend's URL (step 2), plus `VITE_SUPABASE_URL`/`VITE_SUPABASE_ANON_KEY`. Configure the SPA rewrite rule (section 3). Deploy. Note its public URL.
6. **Go back to backend and set `FRONTEND_ORIGIN`** to the frontend URL from step 5. Redeploy.
7. **Smoke test end to end:** load the frontend, sign in, create a meeting, watch it through join → record → upload → transcribe, confirm the webhook round-trip actually reaches backend (check backend logs for the `POST /webhooks/recording-complete` line), and confirm a transcript comes back in the UI.
