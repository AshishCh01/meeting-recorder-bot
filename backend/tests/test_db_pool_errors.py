"""
Database saturation answers 503, not 500.

The pool now fits under Supabase's 15-connection session-mode pooler, which
changes how excess demand fails: it queues in SQLAlchemy's pool instead of
being rejected by Postgres. These pin both shapes to a fast, retryable 503 -
and pin that an unrelated OperationalError is *not* swallowed into one.

Postgres is required (the timeout test exhausts a real pool).
"""
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import database
from app.main import DB_BUSY_DETAIL, app

POOLER_REJECTION = (
    'connection failed: connection to server at "10.0.0.1", port 5432 failed: '
    "FATAL:  (EMAXCONNSESSION) max clients reached in session mode - "
    "max clients are limited to pool_size: 15"
)


@pytest.fixture
def raising_route():
    """Mounts a throwaway route that raises whatever the test hands it."""
    holder = {}

    def endpoint():
        raise holder["make"]()

    app.add_api_route("/__test/db-error", endpoint, methods=["GET"])
    route = app.router.routes[-1]
    try:
        yield holder
    finally:
        app.router.routes.remove(route)


def get(path="/__test/db-error"):
    client = TestClient(app, raise_server_exceptions=False)
    started = time.perf_counter()
    response = client.get(path)
    return response, time.perf_counter() - started


def test_the_engine_is_built_from_the_pool_settings():
    pool = database.engine.pool
    assert pool.size() == settings.db_pool_size
    assert pool._max_overflow == settings.db_max_overflow
    assert pool._timeout == settings.db_pool_timeout_seconds
    # The backend's share of the 15, and a timeout short enough to be a 503
    # rather than SQLAlchemy's 30-second default hang.
    assert (settings.db_pool_size, settings.db_max_overflow) == (8, 0)
    assert settings.db_pool_timeout_seconds < 30


def test_a_real_pool_checkout_timeout_is_a_fast_503(raising_route):
    engine = create_engine(database.engine.url, pool_size=1, max_overflow=0, pool_timeout=0.2)
    held = engine.connect()
    held.execute(text("SELECT 1"))

    def exhaust():
        try:
            engine.connect()
        except Exception as exc:  # the real sqlalchemy.exc.TimeoutError
            return exc
        raise AssertionError("pool with its only connection held still handed one out")

    raising_route["make"] = exhaust
    try:
        response, elapsed = get()
    finally:
        held.close()
        engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"detail": DB_BUSY_DETAIL}
    assert response.headers["retry-after"] == "2"
    assert elapsed < 2, f"503 took {elapsed:.2f}s"


def test_a_pooler_rejection_wrapped_by_sqlalchemy_is_a_503(raising_route):
    # The shape it actually arrives in: SQLAlchemy wraps the DBAPI error.
    raising_route["make"] = lambda: OperationalError(None, None, psycopg.OperationalError(POOLER_REJECTION))

    response, _ = get()

    assert response.status_code == 503
    assert response.json() == {"detail": DB_BUSY_DETAIL}


def test_an_unwrapped_psycopg_pooler_rejection_is_a_503(raising_route):
    raising_route["make"] = lambda: psycopg.OperationalError(POOLER_REJECTION)

    response, _ = get()

    assert response.status_code == 503


@pytest.mark.parametrize("wrapped", [True, False])
def test_an_unrelated_operational_error_is_still_a_500(raising_route, wrapped):
    """A DNS failure is not saturation; telling the client to retry would hide it."""
    dns = psycopg.OperationalError("could not translate host name to address")
    raising_route["make"] = (lambda: OperationalError(None, None, dns)) if wrapped else (lambda: dns)

    response, _ = get()

    assert response.status_code == 500
