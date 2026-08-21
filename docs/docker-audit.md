# Docker Audit

Date: 2026-08-21
Scope: `frontend/Dockerfile`, `backend/Dockerfile`, `meeting-bot/Dockerfile`.
`Dockerfile.dev` files (frontend, backend) are out of scope — `docker-compose.yml` depends on them for local dev and they were not reviewed for production hardening.

Status: report only. No fixes applied.

---

## 1. `frontend/Dockerfile`

**Referenced by compose?** No — `docker-compose.yml` builds `frontend/Dockerfile.dev`. This file appears intended for production deployment (e.g. Render).

### What it does
Two-stage build:
1. `node:20-bookworm-slim` — `npm ci`, copies source, bakes `VITE_API_URL` / `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` in as build `ARG`/`ENV`, runs `npm run build`.
2. `nginx:1.27-alpine` — copies only `/app/dist` from stage 1, writes an inline nginx config with SPA fallback (`try_files ... /index.html`), `EXPOSE 80`, runs `nginx -g daemon off`.

### Findings

| Severity | Finding |
|---|---|
| Info | `VITE_*` values are embedded in the client JS bundle by design — this is normal for a Vite SPA and none of the three (API URL, Supabase URL, Supabase anon key) are secrets meant to stay server-side. Not a leak. |
| Low | Final image's nginx master process starts as root (standard for the stock `nginx:alpine` image; workers drop to the `nginx` user via `nginx.conf`). A stricter posture would use `nginxinc/nginx-unprivileged`, but this is a widely accepted pattern, not a real gap. |

### Recommended fix
None required. Optional: switch base image to `nginxinc/nginx-unprivileged` if a fully rootless container is a hard requirement.

**Verdict: production-ready as-is.**

---

## 2. `backend/Dockerfile`

**Referenced by compose?** No — `docker-compose.yml` builds `backend/Dockerfile.dev`. This file appears intended for production deployment (e.g. Render).

### What it does
Single-stage build:
1. `python:3.12-slim`, installs `build-essential` via apt (cleans apt lists after).
2. `COPY requirements.txt .` → `pip install --no-cache-dir -r requirements.txt`.
3. `COPY . .` (subject to `.dockerignore`).
4. `ENV PYTHONUNBUFFERED=1`, `EXPOSE 8000`.
5. `CMD`: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1` — no `--reload`; single worker is intentional and documented in-file (in-process session/thread-pool state in `chat_service.py` / `transcription_service.py` would fragment across workers).

### Findings

| Severity | Finding |
|---|---|
| **High** | No `USER` directive — the container runs as **root** for its entire lifetime. Any RCE in the app or a dependency runs with root privileges inside the container. |
| Medium | `backend/.dockerignore` excludes `.env`, `.env.*`, `test.db`, `venv/`, `tests/`, `scripts/`, but **not** `recordings/`. The directory is empty today, but nothing stops a local checkout with real recordings from being baked into an image layer on a future build — `COPY . .` would include it. |
| Low | `build-essential` (gcc, etc.) remains in the final image because the build is single-stage. Unnecessary compiler toolchain increases image size and attack surface in a production image; `requirements.txt` deps (psycopg[binary], pgvector, etc.) are prebuilt/pure-Python so the toolchain isn't needed at runtime. |
| Info (confirmed clean) | `.env` is **not** copied into any layer — `.dockerignore` excludes it before the build context is even sent to the daemon. No secrets found baked into this Dockerfile. |
| Info (confirmed clean) | `requirements.txt` contains no dev/test packages (no pytest, etc.) — already a production-only dependency set. |
| Info (confirmed clean) | CMD has no `--reload` flag; worker count is deliberate, not an oversight. |

### Recommended fixes
1. Add a non-root user and `USER` directive; ensure `/app` is owned by that user before the switch (mirrors the pattern already used correctly in `meeting-bot/Dockerfile`).
2. Add `recordings/` to `backend/.dockerignore`.
3. Optional hardening: convert to a multi-stage build so `build-essential` doesn't ship in the final image.

**Verdict: solid foundation, not yet hardened — needs the non-root fix before production deploy.**

---

## 3. `meeting-bot/Dockerfile`

**Referenced by compose?** Yes — this is the file compose actually builds (no `.dev` variant exists for meeting-bot).

### What it does
Single-stage build on `node:22-bookworm-slim`:
1. Installs Xvfb, PulseAudio, dbus-x11, ffmpeg, fonts, and Google Chrome (via apt, with the Google signing key) as root.
2. Creates `botuser` (`useradd --create-home`) — documented reason: PulseAudio's user-mode daemon refuses to start as root, unlike Chrome which is handled via `--no-sandbox`.
3. `npm install` (no `devDependencies` exist in `package.json`, so nothing extra is pulled in), `npx playwright install-deps chromium` (OS-level deps only — the browser itself is the apt-installed Chrome, not a Playwright-downloaded binary).
4. `COPY . .`, then `mkdir -p browser-profiles recordings && chown -R botuser:botuser /app`.
5. Copies and normalizes line endings on `docker-entrypoint.sh`, `chmod +x`.
6. `ENV NODE_ENV=production`, `EXPOSE 3000`, `USER botuser`, `ENTRYPOINT`/`CMD npm start`.

### Findings

| Severity | Finding |
|---|---|
| — | No issues found. |

Specifically verified:
- Non-root: `USER botuser` is set, and ownership (`chown -R botuser:botuser /app`) is applied *before* the switch, so the app is actually usable post-switch — not just a cosmetic `USER` line.
- Secrets: `meeting-bot/.dockerignore` excludes `.env`, `.env.*`, and `auth.json` — confirmed neither is copied into any image layer. `auth.json` is supplied at runtime via a compose bind-mount, never baked in.
- Dev dependencies: `package.json` has no `devDependencies` block, so there's nothing for `npm install` to over-install; `npm ci` vs `npm install` is a reproducibility nicety here, not a security gap.
- Exposed ports: `EXPOSE 3000` matches the app's actual listen port (compose maps `3000:3000`).

### Recommended fix
None. Per review scope, this file is intentionally left unmodified.

**Verdict: already hardened — no changes recommended.**

---

## Summary

| File | Compose uses it? | Verdict | Action needed |
|---|---|---|---|
| `frontend/Dockerfile` | No (compose uses `.dev`) | Production-ready | None |
| `backend/Dockerfile` | No (compose uses `.dev`) | Needs hardening | Add `USER`, add `recordings/` to `.dockerignore`; optional multi-stage cleanup |
| `meeting-bot/Dockerfile` | Yes | Already hardened | None |
| `frontend/Dockerfile.dev`, `backend/Dockerfile.dev` | Yes | Out of scope (local dev only) | Not reviewed |

No files were modified as part of this audit.
