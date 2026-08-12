# Meeting Recorder Bot

Paste a Google Meet, Zoom, or Teams link into the dashboard. A bot joins,
records the meeting, uploads it to Supabase Storage, and the dashboard
shows the finished recording. No AI processing or authentication yet —
recording and storage only, by design.

## Prerequisites

- Node.js 18+
- Python 3.10+
- A Supabase project with:
  - The `meetings` table (`supabase/migrations/create_meetings.sql`)
  - A **private** Storage bucket named `recordings`
  - S3-compatible access keys (Supabase dashboard → Storage → S3 Configuration → Access keys)
- Google Chrome installed (the bot drives your real Chrome, not bundled Chromium)
- ffmpeg installed and on your PATH

## 1. Supabase setup

1. Run `supabase/migrations/create_meetings.sql` in the Supabase SQL editor.
2. Storage → create a bucket called `recordings`, set to **private**.
3. Storage → S3 Configuration → generate an access key pair. Copy the endpoint, access key, and secret — you'll need them for `meeting-bot/.env`.

## 2. meeting-bot setup (run this first — it's the service the backend calls)

```bash
cd meeting-bot
npm install
npx playwright install --with-deps chromium
```

Edit `.env` with your real Supabase S3 credentials and a `BEARER_TOKEN` of your choosing (any random string — the backend must send the same value).

```bash
npm start
```

Runs on `http://localhost:3000`.

## 3. backend setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Edit `.env`:
- `SUPABASE_URL` / `SUPABASE_KEY` — your project's URL and service-role key
- `MEETING_BOT_BEARER_TOKEN` — must match `BEARER_TOKEN` in `meeting-bot/.env`

```bash
uvicorn app.main:app --reload --port 8000
```

Runs on `http://localhost:8000`. Visit `http://localhost:8000/docs` to test endpoints directly.

## 4. frontend setup

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5173`, paste a meeting link, and submit.

## How a meeting flows through the system

1. Frontend `POST /meetings` → backend saves the meeting, detects the platform, calls `meeting-bot`'s `/{platform}/join`.
2. `meeting-bot` joins the call (Playwright), starts `ffmpeg` recording, waits until the meeting ends.
3. On end, it uploads the file to Supabase Storage via the S3-compatible endpoint, then calls the backend's `/webhooks/recording-complete`.
4. Backend generates a signed URL for the recording and updates the meeting's status to `completed`.
5. Frontend polls `GET /meetings` every 5s and shows the finished recording once it appears.

## Known limitations, by design (see project README history)

- **Only Google Meet's selectors are verified** against a real, working join flow. Zoom and Teams bots follow the same interface but their selectors are best-effort — test with `PWDEBUG=1` against a live meeting before relying on them.
- **Teams + Docker**: Chromium in containers has known video-rendering issues with Teams specifically. Test on a bare VM (not containerized) if Teams video capture matters; audio-only tends to be more reliable in Docker.
- **Windows dev vs Linux production**: `AudioCapture.js` handles both, but production should run on Linux with Xvfb + PulseAudio (see the Dockerfile you'll add later), not the Windows VB-Cable workaround used for local development.
- **No auth, no AI processing** — intentionally out of scope for this phase.
