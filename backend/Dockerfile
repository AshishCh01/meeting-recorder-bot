# backend/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# psycopg[binary] and pgvector are pure-python/prebuilt, so no extra
# system build deps are needed beyond a C toolchain for the odd wheel
# that doesn't ship one for your platform.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
# --workers 1 is intentional, not a placeholder: chat_service.py's
# session cache and transcription_service.py's ThreadPoolExecutor both
# live in process memory. Multiple uvicorn workers would each get their
# own copy - a chat session could "forget" earlier turns if routed to
# a different worker mid-conversation. Scale via more container
# replicas behind your platform's load balancer instead.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]