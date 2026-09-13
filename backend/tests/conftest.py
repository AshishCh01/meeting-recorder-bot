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

# Key names only, taken before this file sets anything and before any app.*
# import: exactly what whoever ran pytest (a shell, CI) provided on purpose.
# tests/test_env_isolation.py measures everything else against it.
RUNNER_ENV_KEYS = frozenset(os.environ)

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
# Tests never read backend/.env, by either route it has into the process -
# Settings' env_file and load_dotenv() in database.py both honour this. Without
# it the suite ran whatever configuration the developer's .env held (a queue
# flag flipped there failed four scheduler tests) with real provider keys in
# the process. Set here, before the app.* imports below, because settings is
# built at import time. A test needing a non-default setting sets it with
# monkeypatch.setattr(settings, ...); an override from the shell or CI still
# works, since explicit environment variables are read either way.
os.environ["IGNORE_DOTENV"] = "1"
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
# Phase B1's limiter is off unless a test turns it on. It is an `async def`
# dependency on POST /meetings and both chat routes, so every existing test
# that posts to one of those would otherwise open a connection to
# settings.redis_url - which here is the code default (redis://localhost:6379),
# not TEST_REDIS_URL. It would fail open and those tests would still pass, but
# each would carry a real connection attempt and an error log for a subsystem
# it is not about, and the outcome would quietly depend on whether the machine
# running the suite happens to have a Redis on 6379.
#
# Set here rather than monkeypatched per test for the same reason the stubs
# above are: it is a deliberate choice by the runner, which is exactly what
# test_env_isolation.py's conftest_env_keys means. tests/test_rate_limit.py
# turns it back on with monkeypatch, the way tests/README.md asks.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
# The variables above, set deliberately. Anything else in the environment
# must have come from RUNNER_ENV_KEYS.
CONFTEST_ENV_KEYS = frozenset({
    "DATABASE_URL", "IGNORE_DOTENV",
    "SUPABASE_URL", "SUPABASE_KEY", "MEETING_BOT_BEARER_TOKEN", "GEMINI_API_KEY",
    "RATE_LIMIT_ENABLED",
})

from app.db import database  # noqa: E402
from app.db.models import AiUsageEvent, ChatMessage, Meeting, MeetingChunk, User  # noqa: E402


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


# The tables the suite touches. meeting_chunks is here for the Phase A3
# indexing tests, and it is why the throwaway container must be a pgvector
# image: its embedding column is a Vector(768), which needs the extension.
# chat_messages is here for B3's chat tests, which assert what a turn stores.
# It has to exist for that to mean anything: chat_service swallows a failed
# save, so against a missing table "nothing was stored" would pass for the
# wrong reason.
# ai_usage_events is here for B4: the cost tracker writes a row on every AI
# call, and those writes are swallowed on failure - so without the table every
# test would pass while recording nothing.
_TABLES = [User.__table__, Meeting.__table__, MeetingChunk.__table__, ChatMessage.__table__, AiUsageEvent.__table__]


@pytest.fixture(scope="session")
def runner_env_keys():
    return RUNNER_ENV_KEYS


@pytest.fixture(scope="session")
def conftest_env_keys():
    return CONFTEST_ENV_KEYS


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
        conn.execute(text("TRUNCATE ai_usage_events, chat_messages, meeting_chunks, meetings, users RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def rate_limit_state():
    """
    Phase B1's limiter keeps process-global state - a Redis client cached per
    event loop, and the fail-open counter that decides when to raise a Sentry
    event. Clear both around every test so one test's cached client (pointed
    at whatever redis_url it monkeypatched) and one test's failure count can
    never be inherited by the next.

    Whether the limiter is *on* is set in os.environ above, not here.
    """
    from app.services import rate_limit

    rate_limit.reset_clients()
    rate_limit.reset_failure_stats()
    yield
    rate_limit.reset_clients()
    rate_limit.reset_failure_stats()


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


# ---------------------------------------------------------------------------
# The skip guard (docs/scaling-plan.md, B2)
# ---------------------------------------------------------------------------
#
# Six test files skip some or all of their tests when TEST_REDIS_URL is unset:
# test_rate_limit, test_bot_pool, test_bot_dispatch_queue, test_bot_auth_health,
# test_transcription_queue and test_webhook_enqueue_failure. With Postgres but no Redis this suite reports
# `365 passed, 63 skipped` and exits **0**.
#
# Those 63 are not filler. They are B1's atomicity gate, C3's
# two-hosts-two-meetings gate, C4's per-platform auth gate and A3's
# queue-durability gate - the concurrency work those phases existed to do, and
# the tests least likely to be re-run by hand. A CI workflow that provisions
# Postgres and forgets Redis would be green and blind to all of it, which is
# strictly worse than having no CI: it converts "nobody ran the tests" into
# "the tests passed".
#
# So CI sets PYTEST_REQUIRE_NO_SKIPS=1 and a skip becomes a failure, naming
# every test that skipped and why. Opt-in rather than always-on, because
# locally a partial run is genuinely useful - a developer with no Redis should
# still get the 365 tests that do not need one, and be told what they missed
# rather than handed a red suite.
#
# Deliberately "no skips at all" rather than "no *Redis* skips". This suite has
# no legitimately-conditional test today (with both services: 428 passed, 0
# skipped), so any future skip is a question worth forcing someone to answer in
# a pull request rather than a category to pre-approve here.
_skipped_in_this_run: list = []

REQUIRE_NO_SKIPS = os.environ.get("PYTEST_REQUIRE_NO_SKIPS") == "1"


def pytest_runtest_logreport(report):
    if not REQUIRE_NO_SKIPS or not report.skipped:
        return
    reason = ""
    # A skipif skip arrives as (path, lineno, "Skipped: <reason>").
    if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
        reason = str(report.longrepr[2]).removeprefix("Skipped: ")
    _skipped_in_this_run.append((report.nodeid, reason))


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """
    Turn skips into a failure when PYTEST_REQUIRE_NO_SKIPS=1.

    `session.exitstatus` is what pytest returns to the shell after this hook
    runs, so setting it here is what actually reddens the CI job - printing
    alone would leave the run green. trylast so this lands after the terminal
    summary rather than being scrolled away above it.
    """
    if not REQUIRE_NO_SKIPS or not _skipped_in_this_run:
        return

    print("")
    print(
        "PYTEST_REQUIRE_NO_SKIPS=1 and {} test(s) skipped. In CI a skip means a "
        "service container is missing, so the suite is testing less than the "
        "run claims - see tests/README.md.".format(len(_skipped_in_this_run))
    )
    for nodeid, reason in _skipped_in_this_run:
        print("  SKIPPED {}{}".format(nodeid, " - " + reason if reason else ""))

    session.exitstatus = pytest.ExitCode.TESTS_FAILED

