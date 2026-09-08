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
os.environ.setdefault("SUPABASE_KEY", "stub-key")
os.environ.setdefault("MEETING_BOT_BEARER_TOKEN", "stub-token")
os.environ.setdefault("GEMINI_API_KEY", "stub-key")

from app.db import database  # noqa: E402
from app.db.models import Meeting, User  # noqa: E402


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


# Only the two tables the scheduler touches. Creating all of Base.metadata
# would pull in meeting_chunks, whose pgvector column needs the extension
# installed - irrelevant to anything under test here.
_TABLES = [User.__table__, Meeting.__table__]


@pytest.fixture(scope="session", autouse=True)
def schema():
    _guard_engine()
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
        conn.execute(text("TRUNCATE meetings, users RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db):
    row = User(email="scheduler-test@example.com", bot_display_name="Test Notetaker")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
