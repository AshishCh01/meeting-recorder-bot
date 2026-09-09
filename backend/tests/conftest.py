"""
Test fixtures for the backend suite.

These tests run against a *real* PostgreSQL, not SQLite. The behaviour under
test in test_scheduler_claim.py is two connections racing a conditional
UPDATE: that depends on Postgres row-level locking and READ COMMITTED
re-evaluation, neither of which SQLite reproduces (it serialises writers at
the database level, so the race can't even be staged).

Point TEST_DATABASE_URL at a throwaway database - the suite creates and drops
tables in it. See tests/README.md for a one-liner that starts one.
"""
import os

import pytest

# Everything in this file that touches app.* must happen *after* DATABASE_URL
# is redirected, because app/db/database.py builds its engine at import time
# from the environment. Redirecting it here is also the safety interlock: an
# absent TEST_DATABASE_URL fails the run outright rather than silently
# inheriting the DATABASE_URL in backend/.env, which points at the real
# Supabase database.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set. These tests create and drop tables, so "
        "they refuse to guess at a database. Start a throwaway Postgres and "
        "export it - see tests/README.md."
    )

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# app.config.Settings has required fields with no defaults. Real values live
# in backend/.env, but the tests must not depend on that file existing or
# pull live credentials into a test process - os.environ outranks env_file in
# pydantic-settings, so these placeholders win. Nothing under test calls out.
os.environ.setdefault("SUPABASE_URL", "http://localhost/stub")
# JWT-shaped on purpose: supabase.create_client() runs at import time (via
# app.db.supabase, which transcription_service imports) and regex-validates the
# key before any network call. A plain "stub-key" fails collection outright.
# Not a real credential - the payload decodes to {"role":"anon"} and nothing
# in the suite makes a Supabase request.
os.environ.setdefault(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.c3R1Yi1zaWduYXR1cmU",
)
os.environ.setdefault("MEETING_BOT_BEARER_TOKEN", "stub-token")
os.environ.setdefault("GEMINI_API_KEY", "stub-key")

from app.db import database  # noqa: E402
from app.db.models import Meeting, MeetingChunk, User  # noqa: E402


def _guard_engine() -> None:
    """Last line of defence against running the suite at a real database."""
    url = database.engine.url
    if url.render_as_string(hide_password=True) != _expected_url():
        raise RuntimeError(
            f"engine is pointed at {url.render_as_string(hide_password=True)}, "
            "not TEST_DATABASE_URL - refusing to create/drop tables."
        )
    if url.host and ("supabase" in url.host or "pooler" in url.host):
        raise RuntimeError(f"refusing to run destructive tests against {url.host}")


def _expected_url() -> str:
    from sqlalchemy.engine import make_url
    raw = TEST_DATABASE_URL
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://")
    return make_url(raw).render_as_string(hide_password=True)


# The three tables the suite touches. meeting_chunks is here for the Phase A3
# indexing tests, and it is why the throwaway container must be a pgvector
# image: its embedding column is a Vector(768), which needs the extension.
# chat_messages is still left out - nothing under test writes to it.
_TABLES = [User.__table__, Meeting.__table__, MeetingChunk.__table__]


@pytest.fixture(scope="session", autouse=True)
def schema():
    _guard_engine()
    # meeting_chunks.embedding is a pgvector column, so the type has to exist
    # before create_all emits its DDL. Idempotent, and it fails loudly here
    # rather than as a confusing "type vector does not exist" mid-suite if
    # the container is not a pgvector image.
    with database.engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    database.Base.metadata.drop_all(bind=database.engine, tables=_TABLES)
    database.Base.metadata.create_all(bind=database.engine, tables=_TABLES)
    yield
    database.Base.metadata.drop_all(bind=database.engine, tables=_TABLES)


@pytest.fixture(autouse=True)
def clean_tables():
    """
    Truncate between tests rather than the usual wrap-in-a-transaction trick.
    These tests need genuinely independent connections that really commit -
    a shared outer transaction would hide the very contention being tested.
    """
    yield
    with database.engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("TRUNCATE meeting_chunks, meetings, users RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db):
    row = User(email="backend-test@example.com", bot_display_name="Test Notetaker")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
