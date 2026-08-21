# Render Deploy-Readiness Audit

Date: 2026-08-21
Scope: CORS, environment variables (all three services), the `auth.json` path assumption, and other local-only (docker-compose-shaped) assumptions that won't hold on Render.

Status: item 3 (`auth.json` path) has been applied — diff below. Everything else is report only.

---

## 1. CORS — verified fixed, adding a prod origin needs config only

Confirmed in `backend/app/main.py:44-46` and `backend/app/config.py`:
```python
cors_origins = [settings.frontend_origin]
if settings.environment == "development":
    cors_origins += ["http://localhost:5173", "http://127.0.0.1:5173"]
```
`settings.frontend_origin` is a plain env-driven field (`FRONTEND_ORIGIN`, default `http://localhost:5173`) — **adding your Render frontend URL to CORS is a one-line env var (`FRONTEND_ORIGIN=https://your-frontend.onrender.com`), no code edit required.** `ENVIRONMENT` defaults to `"production"`, so the two hardcoded dev localhost origins are excluded by default and won't leak into the prod allow-list unless you explicitly set `ENVIRONMENT=development`.

**One gap surfaced by the env-var sweep below, worth fixing before deploy:** if `FRONTEND_ORIGIN` is simply forgotten on Render (as opposed to `.env` not existing at all locally), it silently falls back to `http://localhost:5173` — every request from your real deployed frontend will fail CORS with no server-side error at all pointing at the cause. Not a code bug, but an easy footgun during setup — see the env var table below.

---

## 2. Environment variables

### Backend (`backend/app/config.py`, pydantic-settings)

| Var | Status |
|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY`, `MEETING_BOT_BEARER_TOKEN`, `GEMINI_API_KEY` | **Required — fails loudly.** No default; pydantic raises `ValidationError` at import, before the app can even start. |
| `DATABASE_URL` | **Required — fails loudly, but not via pydantic.** Read directly in `backend/app/db/database.py:8-10` via `os.environ.get()`, with an explicit `if not DATABASE_URL: raise ValueError(...)`. Since this module is imported transitively at startup, missing it stops the app just as reliably as a pydantic field — just via a different mechanism. Minor wrinkle: `backend/alembic/env.py:27` reads the same var separately (`os.environ.get("DATABASE_URL", "")`, no validation) — since `alembic upgrade head` runs *before* uvicorn in the Dockerfile `CMD`, a missing `DATABASE_URL` actually surfaces as Alembic's own (less clear) error first, not `database.py`'s explicit message. Not a blocker, just note Alembic's error will be the one you see. |
| `SUPABASE_RECORDINGS_BUCKET` | Default `"recordings"` — safe. |
| `MEETING_BOT_URL` | Default `"http://localhost:3000"`. **Flag:** if forgotten on Render, the backend silently tries to reach `localhost:3000` inside its own container — connection refused, not an "unset env var" error. Must be explicitly set to meeting-bot's real Render URL. |
| `FRONTEND_ORIGIN` | Default `"http://localhost:5173"`. **Flag:** see CORS section above — same silent-wrong-default risk. |
| `ENVIRONMENT` | Default `"production"` — safe, fails closed. |
| Gemini/RAG tuning vars (`GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `RAG_AGENT_MODEL`, `CHUNK_SEGMENTS`, `CHUNK_OVERLAP`, `RETRIEVAL_TOP_K`) | Defaults — safe. |
| `SARVAM_API_KEY`, `JINA_API_KEY` | Default `""`, correctly guarded with truthiness checks before use (`transcription_service.py:285`, `embedding_service.py:138,162`) — an empty string cleanly disables the optional fallback path rather than misbehaving. Safe. |
| `SARVAM_STT_MODEL`, `SARVAM_CHAT_MODEL`, `SARVAM_LANGUAGE_CODE`, `SARVAM_NUM_SPEAKERS`, `JINA_EMBEDDING_MODEL` | Defaults — safe. |
| Watchdog settings (`WATCHDOG_ENABLED`, `WATCHDOG_SWEEP_INTERVAL_MINUTES`, `WATCHDOG_JOINING_TTL_MINUTES`, `MAX_RECORDING_DURATION_MINUTES`, `WATCHDOG_RECORDING_MARGIN_MINUTES`, `WATCHDOG_UPLOADING_TTL_MINUTES`, `WATCHDOG_TRANSCRIBING_TTL_MINUTES`) | Defaults — safe. |

### meeting-bot (`meeting-bot/src/**`) — no startup-validated config object; everything is raw `process.env.X`

| Var | File:line | Behavior if missing |
|---|---|---|
| `PORT` | `index.js:5` | `\|\| 3000` — safe. |
| `AUTH_STATE_PATH` | `BrowserManager.js:15` | **Fixed this session** — falls back to the compose-mount default, then `assertAuthStateExists()` is checked at process startup (`index.js:9-15`) and `process.exit(1)`s with a clear message if the resolved file doesn't exist. See section 3. |
| `BEARER_TOKEN` | `server.js:19-28,39` | **Critical — silently disables auth entirely if unset, does not merely misbehave.** `timingSafeTokenEqual` does `Buffer.from(expected \|\| '', 'utf8')` — if `process.env.BEARER_TOKEN` is `undefined`, this becomes an empty buffer. A request sent with **no** `Authorization` header and no body token produces an equally empty `providedToken` buffer. Comparing two zero-length buffers with `crypto.timingSafeEqual` returns `true`, so `requireAuth` lets the request through with **no credentials presented at all**. I verified this by tracing the exact buffer values by hand — this is not a hypothetical edge case, it's the direct consequence of the `\|\| ''` fallback pattern applied to the *server's own secret*. **This must fail loudly at startup instead** (e.g. `if (!process.env.BEARER_TOKEN) { console.error(...); process.exit(1); }` in `index.js`, alongside the new `AUTH_STATE_PATH` check) — recommend treating this as a pre-deploy blocker, not a nice-to-have. |
| `BEARER_TOKEN` (client side) | `MeetingLifecycle.js:104` | `` `Bearer ${process.env.BEARER_TOKEN}` `` — if unset, sends the literal string `"Bearer undefined"` to the backend webhook. This direction fails safely: the backend's `verify_webhook_token` requires its own required, non-empty `MEETING_BOT_BEARER_TOKEN`, so `"undefined"` simply won't match and the webhook gets a 401. Confusing in logs, not a security gap. |
| `BACKEND_WEBHOOK_URL` | `MeetingLifecycle.js:95` | No null check — `axios.post(undefined, ...)` throws synchronously, caught by the existing retry loop, exhausts 3 retries, then only logs "meeting may be stuck in DB" (the exact gap the watchdog sweep now backstops). Not silent data corruption, but also not a startup failure — recommend validating this at process startup too, same as `BEARER_TOKEN` and `AUTH_STATE_PATH`, rather than discovering it only when the first meeting finishes. |
| `MAX_RECORDING_DURATION_MINUTES` | `MeetingLifecycle.js:28` | `Number(process.env.MAX_RECORDING_DURATION_MINUTES \|\| 90)` — safe fallback. |
| `SUPABASE_URL`, `SUPABASE_KEY` | `SupabaseUploader.js:8-9,13` | No explicit check in this codebase, but `createClient()` throws synchronously if the URL is falsy — since this module loads transitively at process startup (`index.js` → `server.js` → `MeetingLifecycle.js` → `SupabaseUploader.js`), this is accidentally fail-loud (a side effect of the SDK's own constructor, not a deliberate app-level check), with a stack trace pointing into `node_modules` rather than a clear "SUPABASE_URL missing" message. |
| `SUPABASE_RECORDINGS_BUCKET` | `SupabaseUploader.js:10` | `\|\| 'recordings'` — safe, matches backend's default. |

### Frontend (Vite build-time — `frontend/src/**`)

| Var | File:line | Behavior if missing |
|---|---|---|
| `VITE_API_URL` | `lib/api.js:5` | No check — `axios.create({ baseURL: undefined, ... })`. Requests silently resolve relative to the frontend's own origin instead of the backend, no build/runtime error at all. **This is the one genuinely silent failure in the whole app** — nothing crashes, nothing logs, every API call just goes to the wrong place. |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | `lib/supabase.js:3-4,6` | No app-level check, but `createClient()` throws synchronously at module-import time if the URL is missing — the whole app fails to render (blank page + console error), which is de facto fail-loud, again via the SDK, not app code. |

**Deploy-mechanics note:** `frontend/Dockerfile` (the production image) correctly passes all three as `ARG`/`ENV` before `npm run build` (Vite bakes them into the bundle at build time, not read at runtime). On Render this means these three must be supplied as **Docker build args**, not just entries in the service's runtime "Environment" tab — worth double-checking Render's build-arg wiring is actually configured, since a runtime-only env var would silently not reach the built bundle at all.

---

## 3. `auth.json` path — fixed

Applied. `meeting-bot/src/core/BrowserManager.js` now resolves the auth-state path via `process.env.AUTH_STATE_PATH`, falling back to the original compose-mount path (`meeting-bot/auth.json`) so local dev is unchanged. `meeting-bot/src/index.js` calls the new `assertAuthStateExists()` at process startup — before the server even starts listening — and `process.exit(1)`s with a clear message naming the resolved path if the file isn't there, rather than failing deep inside Playwright's `newContext()` on the first meeting join.

```diff
--- a/meeting-bot/src/core/BrowserManager.js
+++ b/meeting-bot/src/core/BrowserManager.js
@@ -1,10 +1,39 @@
 import { chromium } from 'playwright';
+import fs from 'fs';
 import path from 'path';
 import { fileURLToPath } from 'url';
 const __dirname = path.dirname(fileURLToPath(import.meta.url));

+// __dirname is meeting-bot/src/core, so '../..' goes up to the meeting-bot
+// root - that's the docker-compose bind-mount target and the local dev
+// default. On Render (or any host without that bind mount) auth.json is
+// instead delivered as a Secret File at a path Render controls, so it must
+// be overridable via env rather than hardcoded.
+const DEFAULT_AUTH_STATE_PATH = path.join(__dirname, '..', '..', 'auth.json');
+
+export function resolveAuthStatePath() {
+  return process.env.AUTH_STATE_PATH || DEFAULT_AUTH_STATE_PATH;
+}
+
+export function assertAuthStateExists() {
+  const authStatePath = resolveAuthStatePath();
+  if (!fs.existsSync(authStatePath)) {
+    throw new Error(
+      `AUTH_STATE_PATH resolved to "${authStatePath}" but no file exists there. ` +
+      `Set AUTH_STATE_PATH to the Google auth session file's actual location ` +
+      `(e.g. a Render Secret File path), or generate auth.json locally via ` +
+      `generate-auth.cjs if running via docker-compose.`
+    );
+  }
+  return authStatePath;
+}
+
 export class BrowserManager {
   static async launch(profileName = 'default') {
+    const authStatePath = assertAuthStateExists();
     const browser = await chromium.launch({ ... });
     const context = await browser.newContext({
       permissions: ['camera', 'microphone'],
-      storageState: path.join(__dirname, '..', '..', 'auth.json'),
+      storageState: authStatePath,
     });

--- a/meeting-bot/src/index.js
+++ b/meeting-bot/src/index.js
@@ -1,8 +1,19 @@
 import 'dotenv/config';
 import app from './api/server.js';
+import { assertAuthStateExists } from './core/BrowserManager.js';

 const PORT = process.env.PORT || 3000;

+try {
+  const authStatePath = assertAuthStateExists();
+  console.log(`[startup] Using auth state file: ${authStatePath}`);
+} catch (err) {
+  console.error(`[startup] ${err.message}`);
+  process.exit(1);
+}
+
 app.listen(PORT, () => {
   console.log(`meeting-bot listening on port ${PORT}`);
 });
```

Verified both behaviors directly: default path resolves and finds the existing local `auth.json`; a bad `AUTH_STATE_PATH` throws the intended clear error instead of a generic `ENOENT`.

**On Render**, set `AUTH_STATE_PATH` to wherever you mount the auth.json Secret File (Render's Secret Files land at `/etc/secrets/<filename>` by default) — e.g. `AUTH_STATE_PATH=/etc/secrets/auth.json`.

---

## 4. Other local-only assumptions

1. **Docker-compose hostnames baked into local `.env` files (config, not code — but must change):**
   - `backend/.env`: `MEETING_BOT_URL=http://meeting-bot:3000`
   - `meeting-bot/.env`: `BACKEND_WEBHOOK_URL=http://backend:8000/webhooks/recording-complete`

   Both rely on compose's internal DNS resolving bare service names. On Render, each service needs its own reachable URL — either public HTTPS URLs, or Render's private-networking feature (same region/team only, a deliberate opt-in, not equivalent to compose's zero-config bridge network). This needs explicit reconfiguration for both directions of the webhook relationship when you stand up the Render services.

2. **Persistent disk assumptions — mostly fine, one debuggability regression:**
   - meeting-bot's local `recordings/` directory is transient scratch space by design — the file is unlinked after a successful upload (now in its own try/catch per the reliability-audit fix applied earlier). Fine on Render's ephemeral disk.
   - **Exception:** on upload failure, the code deliberately *keeps* the local file for debugging. Under compose this persists via the host bind mount so a developer can inspect it; on Render it lives only in the container's ephemeral filesystem and is lost on the next restart/redeploy. Not a functional bug — recordings are still safely in Supabase for the success path — just a debuggability loss for the failure path specifically.
   - The backend has no runtime-written local storage at all; `static/fonts`/`static/images` are read-only build-time assets, and all recording storage goes through Supabase. No disk durability dependency there.
   - `docker-compose.yml`'s `./recordings:/app/recordings` mount into the **backend** service appears to be dead config — no code in `backend/app/**` references that path at all (likely a leftover from before Supabase-only storage). Not a blocker, just worth pruning if you're translating `docker-compose.yml` into a Render blueprint.

3. **Bind-mount assumptions beyond `auth.json`:** none found. `meeting-bot`'s `recordings/` directory is created on demand via `fs.mkdirSync(..., {recursive:true})` if the mount isn't present, so it degrades gracefully rather than requiring the mount.

4. **Windows/OS-specific code:** confirmed this is a non-issue for the deployed service. `generate-auth.cjs`'s hardcoded `C:\chrome-bot-profile` is a manual local-only tool, never imported by any running service, not part of either Dockerfile. `AudioCapture.js` branches on `os.platform()` for `linux`/`win32`, but meeting-bot only ever actually *runs* inside its Linux-based Docker image (even today, on a Windows dev host) — the Linux/`pulse` branch is the only one that's ever exercised in practice, and it's fully wired: `docker-entrypoint.sh` starts Xvfb + PulseAudio and creates the exact `RecordingSink.monitor` device `AudioCapture.js` expects. Moving to Render's Linux containers exercises the same code path already in continuous use, not a new one. `SoundVolumeView.exe`/`nircmd.exe` have zero code references anywhere (confirmed via full-tree grep) and are being deleted from the repo in your current working tree — not a live concern under any circumstance.

5. **Other topology considerations, not bugs — just things to decide explicitly when setting up Render:**
   - Render services need either public URLs or explicit private-networking configuration between them (point 1) — nothing in the code prevents either choice, but nothing sets it up automatically.
   - `meeting-bot` reads `process.env.PORT` (Render-injection-friendly). The backend's Dockerfile `CMD` hardcodes `--port 8000`, and the frontend's nginx config hardcodes `listen 80` — worth explicitly verifying against how you configure each Render Docker service's port (Render supports either model, but only one will actually route traffic depending on your service settings).
   - The backend's new watchdog sweep (`main.py`'s `lifespan`-managed `asyncio` loop) is tied to the process being alive — if the Render instance spins down on idle (free/starter tier), the watchdog simply pauses until the next request wakes it. This is inherent to the platform tier, not a code defect, and the sweep still runs whenever the instance is up.
   - `--workers 1` (backend) and meeting-bot's single-active-meeting guard are both already accounted for by existing, documented design decisions (in-process session/executor state, and a global `AudioRouter` singleton respectively) — they map cleanly onto one container per service on Render and aren't new problems introduced by this migration.

---

## Appendix: `GOOGLE_API_KEY` vs `GEMINI_API_KEY` — which one is actually used

**Keep `GEMINI_API_KEY`, drop `GOOGLE_API_KEY`.**

Traced this precisely rather than guessing:

- All three places the app actually constructs a Gemini client for real work — `transcription_service.py:24`, `chat_service.py:34`, `embedding_service.py:14` — explicitly pass `api_key=settings.gemini_api_key`. That field is sourced from `GEMINI_API_KEY` only; `GOOGLE_API_KEY` is never read by any of them, directly or indirectly.
- The warning you're seeing (`"Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY."`) comes from `google.genai._api_client.get_env_api_key()` in the SDK itself — but that function only runs when a `genai.Client()` is constructed *without* an explicit `api_key`. None of the three real clients above hit that path.
- What does hit it: `backend/app/rag/agent.py`, which defines `root_agent = Agent(...)` using `google.adk`. ADK's model wrapper reads credentials from the `GOOGLE_API_KEY` environment variable directly rather than accepting an explicit key — which is exactly why line 10 of that file does `os.environ.setdefault("GOOGLE_API_KEY", settings.gemini_api_key)`: a deliberate forwarding shim, so ADK still works even if only `GEMINI_API_KEY` is set. Constructing `Agent(...)` at import time is what triggers the SDK's env-var precedence check and the warning — this happens just from importing `app.rag.agent` (which `chat_service.py` does, to reuse its `INSTRUCTION` constant), regardless of whether the agent is ever actually run.
- **`root_agent` itself is never imported or invoked anywhere else in the codebase** (confirmed via a repo-wide grep) — `chat_service.py` implements its own manual Gemini tool-calling loop directly, not via ADK's `Runner`. It's dead code that still executes its module-level side effects on import.

**Net effect of dropping `GOOGLE_API_KEY`:** none of the app's real behavior changes — `os.environ.setdefault(...)` will populate `GOOGLE_API_KEY` from `GEMINI_API_KEY` automatically at import time (that's precisely what it's for), the SDK's duplicate-key warning disappears since only one is genuinely set beforehand, and the unused `root_agent` continues to construct successfully either way. Safe to remove before deploy.
