# Backend Runtime Filesystem Write Audit

Date: 2026-08-21
Scope: every filesystem path `backend/app/**` and `backend/alembic/**` write to **at runtime** (not build time), checked against the non-root `appuser` added in `backend/Dockerfile` (created via `useradd --create-home`, with `chown -R appuser:appuser /app` applied before `USER appuser`).

Status: report only. No files modified.

---

## Findings

| # | Path | Trigger | Under `/app`? | `appuser` write access? |
|---|---|---|---|---|
| 1 | `tempfile.NamedTemporaryFile(...)` → e.g. `/tmp/tmpXXXXXX.m4a` | `transcription_service.py:189-191` — downloaded meeting audio, written during `transcribe_recording()` (`transcription_service.py:170`) | No — system temp dir (`/tmp`) | **Yes.** `/tmp` is world-writable (mode `1777`) by default on `python:3.12-slim`, independent of the `/app` chown. File is removed via `os.unlink(tmp_path)` in a `finally` block (`transcription_service.py:338-340`). |
| 2 | `tempfile.TemporaryDirectory()` — Sarvam STT fallback output dir | `transcription_fallback_sarvam.py:63-64` — only invoked when Gemini transcription fails repeatedly and `sarvam_api_key` is configured (called from `transcription_service.py:290`) | No — system temp dir (`/tmp`) | **Yes.** Same `/tmp` reasoning; auto-removed on context-manager exit. |
| 3 | `/home/appuser` | Created by `useradd --create-home` in the Dockerfile | No — outside `/app` | **Yes**, though currently unused. `useradd --create-home` gives `appuser` ownership of its own home directory automatically, independent of the `/app` chown. No app code currently writes here. |
| 4 | `/app/**/__pycache__/*.pyc` | Python import machinery, triggered on first import by `alembic upgrade head` and `uvicorn app.main:app` | Yes | **Yes.** Covered by the existing `chown -R appuser:appuser /app`. `PYTHONDONTWRITEBYTECODE` is not set, so these files do get written — harmless, just noting it exists. |
| 5 | Logging | N/A — no file handlers found anywhere in `backend/app/**` | N/A | Not applicable. The app uses `print()` to stdout throughout. `alembic.ini` configures only a `StreamHandler` to stderr (`[handler_console]`) — no `[handler_file]` section, no log file ever created. |
| 6 | PDF generation (`fpdf2`) | `pdf_service.py:48-111` (`generate_meeting_pdf`) | N/A — no disk write | Not applicable. `pdf.output()` result is returned as `bytes()` (line 111) and streamed directly as an HTTP response from `meetings.py:169-176`. `static/fonts` and `static/images` are read-only at runtime (only `add_font`/`image` reads, no writes). |
| 7 | `recordings` (Supabase Storage bucket) | `config.py:7` (`supabase_recordings_bucket`), used via `supabase-py` client in `meetings.py:134`, `transcription_service.py:181-183`, `storage_service.py:12-14` | N/A — remote HTTPS bucket, not a local path | Not applicable. The on-disk `backend/recordings/` directory is not referenced by any Python code at all, and is now excluded from the Docker build context via `backend/.dockerignore`, so it won't exist in the built image. |
| 8 | Alembic state | `alembic upgrade head` (Dockerfile `CMD`) only applies existing migrations | N/A — read-only at runtime | Not applicable. No local state file, no sqlite fallback, no post-write hooks enabled (`[post_write_hooks]` in `alembic.ini` are commented out). `alembic/versions/` is only written by `alembic revision`, which the container never runs. |
| 9 | SQLite | N/A | N/A | Not applicable. `database.py:8-12` requires a Postgres `DATABASE_URL` and raises if unset — no sqlite driver or path anywhere in `backend/app` or `backend/alembic`. `test.db` is dev/test-only and already excluded via `.dockerignore`. |

---

## Net result

**No runtime write fails under the current Dockerfile.** Every write either:
- lands in `/tmp`, which is world-writable regardless of the `/app` chown, or
- lands inside `/app`, which is already covered by `chown -R appuser:appuser /app` before the `USER appuser` switch.

No endpoint, service, or library writes anywhere else — no log files, no on-disk PDF staging, no local `recordings/` writes, no sqlite files, no Alembic state files.

## Low-priority observation (not a failure)

`PYTHONDONTWRITEBYTECODE` is not set in `backend/Dockerfile`, so `.pyc` files get written into `/app/**/__pycache__/` on first import. This is harmless — already covered by the existing chown — noted for completeness only, no action needed.

No files were modified as part of this audit.
