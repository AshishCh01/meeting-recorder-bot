# backend/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# psycopg[binary] and pgvector are pure-python/prebuilt, so no extra
# system build deps are needed beyond a C toolchain for the odd wheel
# that doesn't ship one for your platform. ffmpeg is required at runtime
# by transcription_service.py (_is_audio_silent/_get_audio_duration_seconds
# shell out to ffmpeg/ffprobe) - without it those calls raise
# FileNotFoundError, which is silently swallowed, so silence detection
# just quietly never fires.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --timeout/--retries so a slow PyPI link fails a download rather than
# the whole build. --no-cache-dir stays: this image ships to a server.
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

COPY . .

# Container processes must not run as root - create an unprivileged
# user and hand ownership of /app to it before switching.
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

USER appuser

# Run migrations, then start the API. Safe to run on every boot -
# Alembic no-ops if the schema is already current.
#
# --workers 1 is intentional, not a placeholder. Two of the three
# original reasons have since been fixed (see docs/scaling-plan.md):
# chat history is now persisted in chat_messages and rehydrates into any
# process, and the scheduler claims rows atomically so concurrent sweeps
# cannot double-dispatch a bot. What still forces one worker is
# transcription_service.py's ThreadPoolExecutor: each worker process
# would get its own pool, multiplying concurrent AI load, and in-flight
# jobs die with the process on every deploy.
#
# Phase A3 has landed the queue that fixes this, but the executor is
# still the live path until TRANSCRIPTION_USE_QUEUE=true - that flag is
# deploy 2, deliberately separate from shipping the infrastructure. Once
# it is on and transcription runs in the worker container, this
# constraint is gone and --workers can be raised. Until then, scale with
# container replicas behind a load balancer rather than uvicorn workers.
#
# ${PORT:-8000}: Cloud Run injects PORT and expects the container to
# listen on it, which can differ from the EXPOSE 8000 default below.
# Render and local docker-compose don't set PORT, so this still falls
# back to 8000 for those unchanged.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]