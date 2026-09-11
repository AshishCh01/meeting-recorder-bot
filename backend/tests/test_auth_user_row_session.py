"""
_ensure_user_row must end its transaction on every path out of a cache miss.

get_current_user shares the route's session, so a transaction left open here holds
a connection for the rest of the request - on every route, and for every user at
once right after a restart empties the cache. Covers the existing-user read, the
new-user insert, the insert race (a real IntegrityError, then a second SELECT) and
the email collision that must still raise.

Postgres is required.
"""
import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api import auth
from app.db.database import SessionLocal, engine
from app.db.models import User
from app.main import app

HEADERS = {"Authorization": "Bearer stub-token-identity-is-stubbed"}


def add_user(user_id=None, email=None):
    user_id = user_id or uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(User(id=user_id, email=email or f"u-{user_id}@example.com"))
        db.commit()
    finally:
        db.close()
    return str(user_id)


@pytest.fixture(autouse=True)
def nothing_checked_out_before():
    auth.user_row_cache.clear()
    assert engine.pool.checkedout() == 0, "a previous test leaked a connection"
    yield
    auth.user_row_cache.clear()


# ---------------------------------------------------------------------------
# 2. _ensure_user_row - every way out of a cache miss
# ---------------------------------------------------------------------------

def assert_released(db):
    assert not db.in_transaction(), "a transaction was left open on the request's session"
    assert engine.pool.checkedout() == 0, f"{engine.pool.checkedout()} connection(s) still checked out"


def test_cache_miss_existing_user_leaves_no_transaction_open():
    user_id = add_user()
    db = SessionLocal()
    try:
        auth._ensure_user_row(db, user_id, f"u-{user_id}@example.com")
        assert_released(db)
    finally:
        db.close()
    assert auth.user_row_cache.known(user_id)


def test_cache_miss_new_user_leaves_no_transaction_open():
    user_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        auth._ensure_user_row(db, user_id, "brand-new@example.com")
        assert_released(db)
        assert db.get(User, user_id) is not None
    finally:
        db.close()


def test_cache_miss_insert_race_leaves_no_transaction_open():
    """
    A concurrent request inserts the same user between this SELECT and this
    INSERT: commit raises a real IntegrityError, the function rolls back and
    re-SELECTs. That second SELECT is the easy-to-miss open transaction.
    """
    user_id = str(uuid.uuid4())
    email = f"race-{user_id}@example.com"
    db = SessionLocal()
    real_add = db.add
    raced = []

    def add_after_a_concurrent_insert(obj):
        add_user(user_id, email)  # the other request wins the race, on its own connection
        raced.append(True)
        real_add(obj)

    db.add = add_after_a_concurrent_insert
    try:
        auth._ensure_user_row(db, user_id, email)
        assert raced, "the race was never staged - the test would prove nothing"
        assert_released(db)
    finally:
        db.close()
    assert auth.user_row_cache.known(user_id)


def test_email_collision_still_raises_and_leaves_no_transaction_open():
    """A different user already has this email: not a race, so it must surface."""
    add_user(email="taken@example.com")
    db = SessionLocal()
    try:
        with pytest.raises(IntegrityError):
            auth._ensure_user_row(db, str(uuid.uuid4()), "taken@example.com")
        assert_released(db)
    finally:
        db.close()


@pytest.fixture
def after_auth_route(monkeypatch):
    """A route that makes a 'network call' straight after auth and never queries itself."""
    seen = []

    def endpoint(user_id: str = Depends(auth.get_current_user)):
        seen.append(engine.pool.checkedout())
        return {"user_id": user_id}

    app.add_api_route("/__test/after-auth", endpoint, methods=["GET"])
    route = app.router.routes[-1]
    try:
        yield seen
    finally:
        app.router.routes.remove(route)


def test_a_route_making_a_network_call_after_a_cache_miss_holds_nothing(after_auth_route, monkeypatch):
    user_id = add_user()
    monkeypatch.setattr(auth, "_identify", lambda token: (user_id, f"u-{user_id}@example.com"))

    response = TestClient(app).get("/__test/after-auth", headers=HEADERS)

    assert response.status_code == 200
    assert auth.user_row_cache.known(user_id), "the cache-miss path never ran"
    assert after_auth_route == [0], f"checked out after auth, during the route's call: {after_auth_route}"


