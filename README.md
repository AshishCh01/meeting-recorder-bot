# Meeting Recorder Bot

## 1. What it is

An app that joins your Google Meet / Zoom / Teams calls as an automated bot, records the audio, transcribes it, and gives you a searchable summary with action items and a chat interface to ask questions about what was said.

Three services work together:

- **frontend** — React + Vite. Where you sign up, paste a meeting link to start a recording, and later browse transcripts/summaries and chat with a meeting.
- **backend** — FastAPI. Owns the database, authenticates users, tells `meeting-bot` when to join a meeting, receives the finished recording, and runs transcription + a Gemini-powered RAG chat agent over the transcript.
- **meeting-bot** — Node + Playwright. A real (headed) Chrome browser running in a virtual display, automated to join the meeting, record its audio via FFmpeg, and upload the result.

**End-to-end flow of a recording:**
1. You paste a meeting URL into the frontend. It calls `POST /meetings` on the backend.
2. The backend creates a `meetings` row (`status: scheduled`) and calls `meeting-bot`'s `/google/join` (or `/zoom/join`) endpoint.
3. `meeting-bot` launches Chrome, joins the call, and once admitted, records the audio with FFmpeg until the meeting ends (or a hard time cap).
4. It uploads the recording to Supabase Storage and calls the backend's `/webhooks/recording-complete` webhook.
5. The backend downloads the recording, sends it to Gemini for transcription + analysis (summary, key points, action items, per-speaker timestamps), and indexes it for semantic search.
6. The meeting's status becomes `completed` and the transcript, summary, and chat become available in the frontend.

## 2. Prerequisites

- **Docker Desktop** — everything runs in containers via `docker compose`. Make sure it has a few GB of RAM available; the meeting-bot container runs a real Chrome instance plus FFmpeg.
- **Node.js 20 or later**, installed on your host machine (not just in Docker) — you'll need this once to run `generate-auth.cjs` locally (see section 4), since that step needs a real, visible Chrome window for you to sign in.
- **A Supabase project** (the free tier is fine) — provides the Postgres database, pgvector for semantic search, and file storage for recordings. Create one at [supabase.com](https://supabase.com) if you don't have one.
- **A Gemini API key** — used for transcription, analysis, embeddings, and the chat agent. Get one from [Google AI Studio](https://aistudio.google.com/apikey).
- **A dedicated Google account for the bot** — do **not** use your personal Google account. The bot signs into this account in a real browser and joins meetings as it (visible name: "Meeting Recorder Bot"). Its session gets saved to a file (`auth.json`, see section 4) that effectively holds that account's login — treat the account itself as semi-disposable and never reuse your own.

## 3. Local setup

### Clone the repo

```bash
git clone https://github.com/AshishCh01/meeting-recorder-bot.git
cd meeting-recorder-bot
```

### Set up Supabase

1. In your Supabase project's **SQL Editor**, run the contents of [`supabase/migrations/create_meetings.sql`](supabase/migrations/create_meetings.sql) once. This creates the `meetings` table.
2. In **Storage**, create a new bucket named `recordings` and make it **private** (not public).
3. That's it for manual steps — the rest of the schema (`users`, `meeting_chunks`, indexes, Row Level Security policies, the pgvector extension) is created automatically the first time the backend container starts, via Alembic migrations baked into its startup command. You don't need to run anything else by hand.
4. From your project's **Settings**, collect the values you'll need in the next step:
   - **Settings → API**: Project URL, `service_role` key (secret — backend/meeting-bot use this), `anon` key (public — frontend uses this).
   - **Settings → Database → Connection pooling**: the pooled connection string (`DATABASE_URL`).

### Create each service's `.env`

Each service has a `.env.example` — copy it and fill in real values:

```bash
cp backend/.env.example backend/.env
cp meeting-bot/.env.example meeting-bot/.env
cp frontend/.env.example frontend/.env
```

**`backend/.env`:**

| Variable | Where it comes from |
|---|---|
| `SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `SUPABASE_KEY` | Supabase → Settings → API → `service_role` key (secret) |
| `SUPABASE_RECORDINGS_BUCKET` | leave as `recordings` (the bucket you created above) |
| `DATABASE_URL` | Supabase → Settings → Database → Connection pooling connection string |
| `MEETING_BOT_URL` | leave as `http://meeting-bot:3000` — docker-compose resolves this hostname internally |
| `MEETING_BOT_BEARER_TOKEN` | any long random string you generate yourself, e.g. `openssl rand -hex 32` — **must exactly match** `BEARER_TOKEN` in `meeting-bot/.env` |
| `FRONTEND_ORIGIN` | leave as `http://localhost:5173` for local dev |
| `GEMINI_API_KEY` | Google AI Studio API key |
| `SARVAM_API_KEY`, `JINA_API_KEY` | optional fallback providers — leave blank unless you have accounts with them |

**`meeting-bot/.env`:**

| Variable | Where it comes from |
|---|---|
| `PORT` | leave as `3000` |
| `BEARER_TOKEN` | the **same** random string you used for `MEETING_BOT_BEARER_TOKEN` above |
| `BACKEND_WEBHOOK_URL` | leave as `http://backend:8000/webhooks/recording-complete` |
| `MAX_RECORDING_DURATION_MINUTES` | leave as `90` (a hard cap so a forgotten call doesn't record forever) |
| `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_RECORDINGS_BUCKET` | same values as `backend/.env` — same Supabase project |

**`frontend/.env`:**

| Variable | Where it comes from |
|---|---|
| `VITE_SUPABASE_URL` | same as `backend/.env`'s `SUPABASE_URL` |
| `VITE_SUPABASE_ANON_KEY` | Supabase → Settings → API → `anon` key (**not** the service_role key — this one is meant to be public) |
| `VITE_API_URL` | leave as `http://localhost:8000` for local dev |

### Build and start

```bash
docker compose up -d --build
```

This builds all three images and starts them. First run takes a few minutes (installing Chrome, Playwright, Python/Node dependencies).

### Verify each service came up

```bash
# Backend - should return {"status":"ok"}
curl http://localhost:8000/health

# meeting-bot - should return {"status":"ok"}
curl http://localhost:3000/health

# Frontend - open in a browser
# http://localhost:5173
```

If something's not responding, check its logs:

```bash
docker compose logs backend
docker compose logs meeting-bot
docker compose logs frontend
```

A healthy backend log ends with something like `Application startup complete` after a line showing Alembic applying migrations. A healthy meeting-bot log shows `[startup] Using auth state file: /app/auth.json` (see next section) and `meeting-bot listening on port 3000`.

## 4. The `auth.json` step

The bot needs to actually be signed into the dedicated Google account from section 2 in order to join meetings as a trusted participant instead of an anonymous guest. Rather than storing a password anywhere, it stores a **saved browser session** — cookies captured from a real, one-time interactive login — in a file called `auth.json`.

### Generating it

From the `meeting-bot` directory, on your **host machine** (not inside Docker — this needs a real, visible browser window):

```bash
cd meeting-bot
npm install
npx playwright install chrome
node generate-auth.cjs
```

This opens a real Chrome window, navigates to Google Meet, and waits for you to sign into the dedicated bot account by hand (including any 2FA). Once you see the account is logged in and the terminal prompts you to, click back on the terminal and press Enter. The script saves the session to `meeting-bot/auth.json` and prints which critical session cookies it captured.

### Where it must live

`meeting-bot/auth.json`, at the root of the `meeting-bot/` directory. `docker-compose.yml` bind-mounts this exact path into the container, so the running bot always sees whatever's currently on disk — no rebuild needed after regenerating it.

### It expires — this is normal, not a bug

Google issues the long-lived part of the session with a lifetime measured in months, but a short-lived companion set of "session rotation" cookies is only valid for about **10 minutes** after generation. The bot automatically refreshes those cookies every time it successfully confirms the session is live, which extends how long a generated `auth.json` stays usable — but if the bot goes a long stretch without joining any meeting, or if you sign that account out somewhere else, it'll go stale again and you'll need to re-run `node generate-auth.cjs`.

### The expected fallback: guest join + a human clicking Admit

If `auth.json` isn't authenticating for whatever reason, the bot doesn't just fail — it falls back to joining as an **anonymous guest**, using Google Meet's normal "Ask to join" flow, the same as anyone without an account clicking a meeting link. **This requires a human already in the call to click "Admit."** That's expected, working behavior, not a failure state — it's simply a lower-trust way of getting the bot into the meeting. The one thing worth knowing: some meetings (particularly ones hosted by a Workspace account with stricter guest settings) don't allow anonymous participants at all, and will reject the guest join outright rather than queue it for admission — see Troubleshooting below for what that looks like.

## 5. How to use it

1. Open `http://localhost:5173`, go to **Register**, and sign up with an email and password.
2. Log in, and from the **Dashboard**, click to start a new meeting and paste a Google Meet / Zoom / Teams URL.
3. The bot joins automatically. If it's using a valid `auth.json`, it joins as the trusted bot account and typically gets in on its own (some meetings still show a brief "Ask to join" even for signed-in accounts). If it fell back to guest mode, **a human in the call needs to click Admit** for the bot to get in.
4. Once admitted, the bot silently records until the meeting ends (or the 90-minute cap is hit), then leaves and uploads the recording.
5. Back in the dashboard, the meeting's status moves through `recording → uploading → transcribing → completed` automatically — refresh or revisit the meeting to see progress. Once `completed`, you get a summary, key points, action items with owners and timestamps, a full per-speaker transcript, a PDF export option, and a chat box to ask questions like "what did Alice say about the budget?" — answered by a Gemini agent grounded in that meeting's transcript.

## 6. Deployment

Full step-by-step instructions — every environment variable per service, exact Dockerfile/build settings, and the order to create services in given they depend on each other's URLs — are in **[`docs/render-deploy.md`](docs/render-deploy.md)**. Read that before deploying; this is just the shape of it:

- **frontend** deploys as a Render **Static Site** (build command `npm ci && npm run build`, publish directory `dist` — Docker isn't used for this one).
- **backend** deploys as a Render **Docker Web Service** using `backend/Dockerfile` (not `Dockerfile.dev`).
- **meeting-bot** deploys as a Render **Docker Private Service** using `meeting-bot/Dockerfile`, with `auth.json` uploaded as a Render **Secret File** — it must be attached *before* the first deploy, since the service now refuses to start without it.
- Because each service needs another's URL (`MEETING_BOT_URL`, `BACKEND_WEBHOOK_URL`, `FRONTEND_ORIGIN`, `VITE_API_URL`), they need to be created in a specific order and a couple of backend env vars get patched in after the other two exist. `docs/render-deploy.md` section 5 spells out the exact sequence.

## 7. Troubleshooting

**Bot joins but is never admitted**
Logs show:
```
[Lifecycle] Process interrupted or errored: Not admitted to the meeting within the timeout window
```
with a screenshot saved as `google-meet-admission-timeout.png` inside the container. Usually means the bot is in the waiting room (guest mode, see section 4) and nobody clicked Admit within 2 minutes — go click it. If it happens even when someone was watching for it, check whether `auth.json` is authenticating at all (next item).

**`auth.json` has gone stale / isn't authenticating**
Logs show either:
```
AUTH_EXPIRED: Google session in auth.json is no longer valid — regenerate auth.json
```
(screenshot `google-meet-auth-expired.png`), or the subtler version where Google shows no explicit error at all and just silently falls back to an anonymous join:
```
AUTH_EXPIRED: auth.json did not authenticate — Meet is showing the anonymous "Ask to join" flow with a Sign in prompt...
```
(screenshot `google-meet-anonymous-session.png`). Either way: `cd meeting-bot && node generate-auth.cjs` and try again — see section 4.

**Migrations didn't apply / backend won't start**
The backend runs `alembic upgrade head` automatically before starting uvicorn (see its `CMD`), so a broken `DATABASE_URL` shows up immediately in `docker compose logs backend` as either an Alembic connection error, or — if the variable is missing entirely — a clear `pydantic.ValidationError` listing every required env var that's unset. Double-check `backend/.env`'s `DATABASE_URL` is the Supabase **pooler** connection string, not the direct one, and that the password in it is correct.

**CORS errors in the browser console**
Something like `has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present`. This means the backend's `FRONTEND_ORIGIN` doesn't match the origin the frontend is actually being served from. Locally this should just be `http://localhost:5173` (the default); if you changed the frontend's port or are deploying, make sure `FRONTEND_ORIGIN` on the backend matches exactly (scheme + host + port, no trailing slash).

## 8. Known limitations and deferred work

Pulled from the audit reports in `docs/` — these are known, intentionally not yet fixed:

- **Backend Docker image isn't multi-stage.** `build-essential` (the C compiler toolchain, needed only to build a couple of Python wheels) ships in the final production image instead of being stripped after `pip install`. Bigger image, slightly larger attack surface than necessary — not a functional issue. (`docs/docker-audit.md`)
- **`auth.json` is still exposed in git history.** It was committed once early in the project and has since been removed from tracking going forward, but the original commit is still reachable in history and already pushed to the remote — the credential it represents should be treated as compromised until that history is purged and the bot's Google account session is rotated. (`docs/secrets-audit.md`)
- **Speaker-name search doesn't escape SQL `LIKE` wildcards.** The chat agent's "what did X say" tool builds an `ILIKE '%name%'` pattern from a user-supplied speaker name without escaping `%`/`_`/`\` — a speaker name containing those characters can broaden the match beyond the intended person (scoped to the asking user's own meeting only, not a cross-user issue). (`docs/validation-audit.md`)
- **No length limit on chat questions.** `ChatRequest.question` has no `max_length`, so an authenticated user can send an arbitrarily long question straight through to Gemini — a cost/robustness concern, not a security one. (`docs/validation-audit.md`)
- **Microsoft Teams is detected but not implemented.** The platform detector recognizes `teams.microsoft.com`/`teams.live.com` URLs and will accept them from `POST /meetings`, but there's no `TeamsBot` class — the meeting-bot lifecycle fails with `Unsupported platform: teams` and the meeting is marked failed. Only Google Meet and Zoom actually work end to end.

See the rest of `docs/*.md` for the full audit trail (Dockerfile hardening, RLS policies, API auth, input validation, reliability, deploy readiness) if you want more context on any of the above or on decisions made along the way.
