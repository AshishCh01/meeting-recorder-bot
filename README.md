# MeetIQ — Meeting Recorder Bot

## 1. What it is

MeetIQ sends an automated bot into your Google Meet or Zoom calls. The bot records the audio, and the app transcribes it and gives you a searchable summary with action items, plus a chat box where you can ask questions about what was said.

The stack is six containers:

- **frontend**: React + Vite (nginx in production). You sign up here, paste a meeting link or pick an event from your Google Calendar, browse transcripts and summaries, and chat with a meeting.
- **backend**: FastAPI. It owns the database, authenticates users (Supabase JWTs, verified locally), picks a recorder for each meeting, receives the finished recording, and serves the Gemini-powered RAG chat agent. It also runs the scheduler (calendar-booked meetings) and the watchdog (fails meetings stuck in a status).
- **worker**: the backend image running `arq app.worker.WorkerSettings` instead of uvicorn. It runs transcription jobs and, when the dispatch queue is on, bot-dispatch jobs and the bot-pool heartbeat.
- **redis**: broker for the arq queues and backing store for per-user rate limiting.
- **meeting-bot** / **meeting-bot-2**: Node + Playwright. Each one is a *recorder host*: a real headed Chrome on an Xvfb virtual display, automated to join the meeting and record its audio through PulseAudio + FFmpeg. Each host signs in with its **own** Google and Zoom accounts. Local dev runs two hosts (`bot-a`, `bot-b`); the production compose file runs one.

**How a recording flows through the system:**
1. You paste a meeting URL (or schedule a calendar event). The frontend calls `POST /meetings`.
2. The backend creates a `meetings` row and picks a recorder host from `BOT_HOSTS` that has a free slot and a live session for that platform. It then calls that host's `/google/join` or `/zoom/join`. If every recorder is busy and `BOT_DISPATCH_USE_QUEUE=true`, the meeting sits in `queued` until one frees up.
3. The recorder launches Chrome with its saved session, joins the call, waits to be admitted, and records until the meeting ends or it hits `MAX_RECORDING_DURATION_MINUTES`. It sends progress pings to the backend along the way.
4. It uploads the recording to Supabase Storage and calls the backend's `/webhooks/recording-complete`.
5. The backend hands transcription off: to the arq worker when `TRANSCRIPTION_USE_QUEUE=true`, otherwise to an in-process thread pool. Gemini transcribes and analyses the audio (summary, key points, action items, per-speaker timestamps), and the transcript is chunked, embedded, and indexed in pgvector.
6. The meeting becomes `completed`. The transcript, summary, PDF export, audio playback, and chat are now available in the frontend.

Meeting statuses: `scheduled → queued → joining → waiting_for_admission → recording → uploading → transcribing → completed` (or `failed`).

**AI provider fallbacks.** Each fallback is optional and turns on when you set its key:

| Step | Primary | Fallback when Gemini keeps returning 429/503/504 |
|---|---|---|
| Transcription | Gemini | Sarvam (`SARVAM_API_KEY`) |
| Embeddings | Gemini | Jina (`JINA_API_KEY`) |
| Chat | Gemini | Groq (`GROQ_API_KEY`, the model must support tool calling) |

The `ai_usage_events` table records every AI call, including tokens, audio seconds, and estimated USD cost. Ready-made spend queries are in [`backend/sql/ai_usage_queries.sql`](backend/sql/ai_usage_queries.sql).

## 2. Prerequisites

- **Docker Desktop** with plenty of RAM. Local dev runs **two** recorder containers, and each runs a real Chrome plus FFmpeg (budget ~2 GB of shared memory per recorder).
- **Node.js 22** on your host machine, not only inside Docker. You need it to run the session generators (section 4), which open a real, visible Chrome window. **Google Chrome** must be installed on the host too.
- **A Supabase project** (the free tier is fine). It provides Postgres, pgvector, auth, and file storage.
- **A Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey).
- **Dedicated bot accounts, one set per recorder host.** Each host needs its own Google account, plus its own Zoom account if you record Zoom. **Never use your personal account, and never share one account between two hosts**: two concurrent sessions rotate the same short-lived cookies against each other and both go stale. For local dev that means two Google accounts (`bot-a`, `bot-b`).
- *Optional:* Groq / Jina / Sarvam keys (fallbacks), a Google Cloud OAuth client (Calendar integration), and a Sentry DSN (error reporting).

## 3. Local setup

### Clone the repo

```bash
git clone https://github.com/AshishCh01/meeting-recorder-bot.git
cd meeting-recorder-bot
```

### Set up Supabase

1. In **Storage**, create a **private** bucket named `recordings`.
2. You don't need to run any SQL. The backend runs `alembic upgrade head` on every start, which creates the whole schema: `users`, `meetings`, `meeting_chunks` (pgvector), `chat_messages`, `calendar_connections`, `ai_usage_events`, indexes, and Row Level Security policies.
3. Collect these values for the next step:
   - **Settings → API**: Project URL, the `service_role` key (secret, used by the backend and the recorders), and the `anon` key (public, used by the frontend).
   - **Settings → Database → Connection pooling**: the **session-mode pooler** connection string (port 5432) for `DATABASE_URL`.

### Create each service's `.env`

```bash
cp backend/.env.example backend/.env
cp meeting-bot/.env.example meeting-bot/.env
cp frontend/.env.example frontend/.env
```

Every `.env.example` is commented. These are the values you must fill in:

**`backend/.env`** (the worker shares this file):

| Variable | Value |
|---|---|
| `SUPABASE_URL` | Supabase Project URL |
| `SUPABASE_KEY` | Supabase `service_role` key |
| `SUPABASE_RECORDINGS_BUCKET` | `recordings` |
| `DATABASE_URL` | Supabase session-mode pooler connection string |
| `MEETING_BOT_BEARER_TOKEN` | a long random string, e.g. `openssl rand -hex 32`. It **must match** `BEARER_TOKEN` in `meeting-bot/.env` |
| `GEMINI_API_KEY` | Google AI Studio key |
| `FRONTEND_ORIGIN` | `http://localhost:5173` |
| `ENVIRONMENT` | set to `development` locally (it defaults to `production`, which disables the localhost CORS origins) |
| `REDIS_URL` | leave as `redis://redis:6379` |

You can leave the rest at their defaults. The ones worth knowing about:

| Variable | Default | What it does |
|---|---|---|
| `TRANSCRIPTION_USE_QUEUE` | `false` | `true` runs transcription in the `worker` container (survives backend restarts). `false` runs it in-process. |
| `BOT_DISPATCH_USE_QUEUE` | `false` | `true`: a meeting requested while every recorder is busy waits in `queued` (up to ~20 min). `false`: it fails immediately. |
| `RATE_LIMIT_ENABLED` | `true` | Per-user limits: 30 chat questions and 10 meeting creations per 5 minutes. |
| `JWT_LOCAL_VERIFICATION_ENABLED` / `AUTH_FALLBACK_ENABLED` | `true` / `true` | Verify Supabase ES256 JWTs locally against JWKS, and fall back to asking Supabase if that fails. |
| `DB_POOL_SIZE` | `8` | The backend's share of Supabase's 15-connection pooler cap. The worker's share is set in the compose files. |
| `SARVAM_API_KEY`, `JINA_API_KEY`, `GROQ_API_KEY` | blank | Fallback providers (see section 1). |
| `SENTRY_DSN` | blank | Error reporting, off when blank. |
| `GEMINI_*_COST_PER_MTOK`, etc. | see file | Rates used for `ai_usage_events` cost estimates. |

`BOT_HOSTS` (the recorder list) is set in `docker-compose.yml`, not in `.env`. When it's empty, the pool is a single host at `MEETING_BOT_URL`.

**Optional: Google Calendar integration** ("Upcoming" page). Leave these blank to disable it. See [`docs/google-calendar-integration-plan.md`](docs/google-calendar-integration-plan.md).

| Variable | Value |
|---|---|
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud Console → APIs & Services → Credentials → OAuth client ID (Web application) |
| `GOOGLE_OAUTH_REDIRECT_URI` | `http://localhost:8000/calendar/oauth/callback` |
| `GOOGLE_TOKEN_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

**`meeting-bot/.env`** (both recorder containers share this file):

| Variable | Value |
|---|---|
| `BEARER_TOKEN` | the **same** string as `MEETING_BOT_BEARER_TOKEN` |
| `BACKEND_WEBHOOK_URL` | `http://backend:8000/webhooks/recording-complete` |
| `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_RECORDINGS_BUCKET` | same values as the backend |
| `MAX_RECORDING_DURATION_MINUTES` | `90` (hard cap per recording) |
| `MAX_CONCURRENT_MEETINGS` | `1` (overridden per container in the compose files) |
| `AUTH_KEEPALIVE_ENABLED` / `AUTH_KEEPALIVE_INTERVAL_MINUTES` | `true` / `15`: periodically refreshes the saved sessions so they don't expire between meetings |
| `DEBUG_SCREENSHOTS` | `false`. Set `true` only for local troubleshooting, because the screenshots capture real meeting content. |
| `AUTH_STATE_PATH`, `ZOOM_AUTH_STATE_PATH` | leave unset. The compose files set them to each host's mounted `auth/` directory. |

**`frontend/.env`:**

| Variable | Value |
|---|---|
| `VITE_SUPABASE_URL` | Supabase Project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase `anon` key (**not** the service_role key) |
| `VITE_API_URL` | `http://localhost:8000` |

### Generate the bot sessions

Do this **before** starting the stack. A recorder refuses to start without its Google `auth.json`. See section 4.

### Build and start

```bash
docker compose up -d --build
```

The first build takes several minutes because it installs Chrome, Playwright, and the Python/Node dependencies. `docker-compose.yml` is the dev stack: the backend, worker, and frontend bind-mount your source and hot-reload.

If you only have credentials for one recorder, start everything except `meeting-bot-2`:

```bash
docker compose up -d --build backend worker redis meeting-bot frontend
```

`bot-b` never answers a heartbeat, so the backend drops it from rotation within about 20 seconds.

### Verify

```bash
curl http://localhost:8000/health   # backend     -> {"status":"ok"}
curl http://localhost:3000/health   # meeting-bot (bot-a)
curl http://localhost:3001/health   # meeting-bot-2 (bot-b)
# frontend: http://localhost:5173
```

Check the logs if something doesn't come up:

```bash
docker compose logs -f backend worker meeting-bot meeting-bot-2
```

- A healthy backend shows Alembic migrations running, then `Application startup complete`.
- A healthy recorder prints one `[startup] auth state google: /app/auth/auth.json (… bytes, sha256:…)` line per platform, then `meeting-bot listening on port 3000`.

## 4. Bot sessions (`auth.json` / `zoom-auth.json`)

Each recorder joins meetings as a signed-in account instead of an anonymous guest. No password is stored anywhere. Instead, the recorder keeps a **saved browser session** (cookies captured from a one-time interactive login) in a JSON file per platform.

### Layout

One directory per recorder host. Everything in it except the README is gitignored:

```
meeting-bot/auth/
  bot-a/
    auth.json        # Google session for bot-a  -> mounted into meeting-bot
    zoom-auth.json   # Zoom session for bot-a (optional)
  bot-b/
    auth.json        # a DIFFERENT Google account -> mounted into meeting-bot-2
    zoom-auth.json
```

Compose bind-mounts each directory at `/app/auth` in its container, so a regenerated file is picked up without a rebuild. If you run `node src/index.js` directly without `AUTH_STATE_PATH` set, the recorder falls back to `meeting-bot/auth.json` and `meeting-bot/zoom-auth.json`.

### Generating them

Run these from `meeting-bot/` on your **host machine**. They open a real Chrome window, so they can't run inside Docker.

```bash
cd meeting-bot
npm install
```

`generate-auth.cjs` launches Chrome from a **dedicated Chrome profile directory**. Chrome refuses automation on your real default profile. Before running the script, edit `userDataDir` and `profileDir` near the top of the script to point at the profile for the account you're capturing.

Then run the generators, passing the output path as an environment variable:

```powershell
# PowerShell
$env:AUTH_STATE_PATH="auth/bot-a/auth.json"; node generate-auth.cjs
$env:ZOOM_AUTH_STATE_PATH="auth/bot-a/zoom-auth.json"; node generate-zoom-auth.cjs
```

```bash
# bash
AUTH_STATE_PATH=auth/bot-b/auth.json node generate-auth.cjs
ZOOM_AUTH_STATE_PATH=auth/bot-b/zoom-auth.json node generate-zoom-auth.cjs
```

For each one, sign in by hand in the Chrome window (including 2FA), then return to the terminal and press Enter when it prompts you. The script writes the file and prints its absolute path along with the session cookies it captured. **Use a different account for each host.** More detail is in [`meeting-bot/auth/README.md`](meeting-bot/auth/README.md).

These files are live credentials: anyone holding one is signed in as that bot account.

### Keeping them alive

Google's long-lived cookies last months, but its session-rotation cookies expire within minutes to an hour. Each recorder's **AuthKeepAlive** job opens Chrome every `AUTH_KEEPALIVE_INTERVAL_MINUTES` (15 by default), confirms that the session is still live, and writes the rotated cookies back to the mounted file. Successful joins refresh the file as well.

Each recorder reports the health of each platform's session on `GET /capacity`. The backend stops sending **Google** meetings to a host whose Google session is dead, but keeps sending it Zoom meetings, and the reverse. When a session dies, the recorder logs an `ALERT` naming the fix. Runbook: [`docs/auth-keepalive-runbook.md`](docs/auth-keepalive-runbook.md).

### There is no silent guest fallback for Google Meet

If `auth.json` doesn't authenticate, Meet shows the anonymous "Ask to join" flow. Many hosts deny that flow outright, so the recorder treats it as a failure: the join fails with `AUTH_EXPIRED` and the meeting is marked failed with a message telling you to regenerate the file. The fix is always to re-run the generator for that host.

## 5. How to use it

1. Open `http://localhost:5173`, **Register** with an email and password, and log in.
2. **Dashboard:** paste a Google Meet or Zoom link to record a meeting now. Teams links are rejected because there is no Teams recorder.
3. **Upcoming** (requires the Calendar integration): connect Google Calendar and choose which events to record. The scheduler dispatches a recorder a couple of minutes before each event starts.
4. **Settings:** change the bot's display name in meetings (`bot_display_name`).
5. The bot joins as its signed-in account. Some meetings still require someone in the call to click **Admit**. The recorder waits up to 5 minutes before giving up.
6. The meeting moves through its statuses automatically. You can **Stop** a meeting that's recording, **Retry** a failed one, or delete it.
7. When it's `completed`, you get a summary, key points, action items with owners and timestamps, a per-speaker transcript, audio playback, a PDF export, and a chat panel for questions like "what did Alice say about the budget?". Chat answers stream in, are grounded in that meeting's transcript, and are saved.

## 6. Tests and CI

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs both suites on every push to `main` and every pull request. CI uses no secrets and fails if any test is skipped.

**Backend** (Python 3.12, pytest). The suite needs a throwaway pgvector Postgres and Redis. It never reads `backend/.env`. Full instructions and a coverage map are in [`backend/tests/README.md`](backend/tests/README.md).

```bash
docker run -d --rm --name meetiq-test-pg -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=meetiq_test -p 55432:5432 pgvector/pgvector:pg18
docker run -d --rm --name meetiq-test-redis -p 56379:6379 redis:7-alpine

cd backend
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql://postgres:testpw@localhost:55432/meetiq_test \
TEST_REDIS_URL=redis://localhost:56379 \
  python -m pytest
```

**meeting-bot** (Node 22, `node --test`):

```bash
cd meeting-bot
npm test
```

## 7. Deployment

The supported target is a **single AWS EC2 instance** running `docker-compose.prod.yml`. That is a standalone production compose file, not an override of the dev file. Follow **[`docs/aws-ec2-deploy.md`](docs/aws-ec2-deploy.md)** for instance sizing, the systemd unit, and the redeploy steps. In outline:

- Production images (`backend/Dockerfile`, `frontend/Dockerfile`), with `restart: unless-stopped` on every service.
- **frontend** is built as a static bundle and served by nginx on port 80. The `VITE_*` values are **build args** baked in at `docker compose build` time, so export them into your shell from `frontend/.env` before building.
- **backend** runs migrations and then uvicorn on port 8000. **worker** and **redis** run alongside it; Redis is never published to the host.
- **meeting-bot** runs one recorder (`bot-a`, credentials in `meeting-bot/auth/bot-a/`) with no published port. The backend reaches it at `http://meeting-bot:3000`.
- Set production values for `FRONTEND_ORIGIN`, `VITE_API_URL`, `GOOGLE_OAUTH_REDIRECT_URI`, and `ENVIRONMENT=production`.

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Before scaling the worker (`--scale worker=2`) or adding backend replicas, read [`docs/scaling-plan.md`](docs/scaling-plan.md). Each extra process needs a smaller share of the 15-connection database pool.

[`docs/render-deploy.md`](docs/render-deploy.md) is an older Render checklist from before Redis, the worker, and the bot pool existed. It is kept for reference only.

## 8. Troubleshooting

**A recorder container exits immediately.**
Its log shows `[startup] …` naming a missing environment variable or a missing `auth.json`. Generate the session for that host (section 4), or start the stack without `meeting-bot-2`.

**Meeting fails with `AUTH_EXPIRED`.**
The host's Google session is dead:
```
AUTH_EXPIRED: Google session in auth.json is no longer valid — regenerate auth.json
AUTH_EXPIRED: auth.json did not authenticate — Meet is showing the anonymous "Ask to join" flow…
```
Re-run `generate-auth.cjs` with that host's `AUTH_STATE_PATH`. You don't need to restart anything: the next keepalive cycle (or heartbeat) returns the host to rotation.

**Meeting fails because no recorder is available.**
Every host is busy or has a dead session for that platform, and the failure message says which. Wait for a meeting to finish, fix the dead session, or set `BOT_DISPATCH_USE_QUEUE=true` so requests queue. After changing `.env`, run `docker compose up -d` to recreate the containers; `restart` keeps the old environment.

**Bot joins but is never admitted.**
Nobody clicked Admit within 5 minutes, or the meeting blocks outside accounts. For Zoom, "Automated bots aren't allowed to join" means that host needs a valid `zoom-auth.json`. To see what the bot saw, set `DEBUG_SCREENSHOTS=true` locally.

**Meeting stuck in `transcribing`.**
With `TRANSCRIPTION_USE_QUEUE=true`, check `docker compose logs worker` and confirm that `redis` is up. Either way, the watchdog marks the meeting failed after 30 minutes, and **Retry** re-runs it without re-billing a transcription that already succeeded.

**Backend won't start or migrations fail.**
A missing variable produces a `pydantic.ValidationError` listing every required unset field. A connection error usually means `DATABASE_URL` isn't the session-mode pooler string or has the wrong password. `EMAXCONNSESSION` or API 503s mean the processes together are using more than Supabase's 15 pooled connections; see the `DB_POOL_SIZE` comments in `backend/.env.example`.

**CORS errors in the browser.**
`FRONTEND_ORIGIN` must exactly match the page's origin (scheme, host, and port, with no trailing slash). For local dev, also set `ENVIRONMENT=development`.

**`429 Too Many Requests`.**
The per-user rate limit is working as intended. Adjust `CHAT_RATE_LIMIT_*` or `MEETING_CREATE_RATE_LIMIT_*`, or set `RATE_LIMIT_ENABLED=false`.

## 9. Known limitations

- **Microsoft Teams isn't supported.** The backend rejects Teams URLs because there is no Teams recorder.
- **Backend sweep loops aren't confined to one replica yet** (scaling plan, Phase A2). Running more than one backend replica needs a load balancer and that change. It is blocked on moving off a single EC2 instance.
- **Deploying a new meeting-bot while it is recording loses that recording.** The recorder has no graceful-shutdown path.
- **Speaker-name search doesn't escape SQL `LIKE` wildcards.** In the chat agent's "what did X say" tool, a `%` or `_` in a speaker name can match more speakers than intended. This only affects the asking user's own meeting.
- **Backend image isn't multi-stage.** `build-essential` ships in the production image.
- **`auth.json` exists in old git history.** A session file was committed early on. That account's credentials have been rotated, so the file is a dead token, and the history was left as-is because the repo is private. Revisit this if the repo's visibility changes.
- **`MAX_CONCURRENT_MEETINGS > 1` shares one account within a host.** Scale by adding hosts, each with its own accounts, not by raising the per-host cap.

The audit trail and design decisions are in `docs/`. Start with [`docs/scaling-plan.md`](docs/scaling-plan.md) and [`docs/audit-report-2026-09-11.md`](docs/audit-report-2026-09-11.md).
