"""
get_meeting must not hold a pooled connection while it signs the recording URL.

The stub replaces get_signed_recording_url (a Supabase Storage request) and reads
engine.pool.checkedout() from inside it, on a user-row cache hit and a miss.

Postgres is required.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import auth, meetings
from app.db.database import SessionLocal, engine
from app.db.models import Meeting, MeetingChunk, User
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


def add_meeting(user_id, status="completed", embedding_provider=None, chunks=()):
    meeting_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(Meeting(id=meeting_id, user_id=user_id, meeting_url="https://zoom.us/j/1", platform="zoom",
                       status=status, title="Planning", embedding_provider=embedding_provider))
        db.flush()
        for i, (content, embedding) in enumerate(chunks):
            db.add(MeetingChunk(meeting_id=meeting_id, chunk_index=i, content=content, speakers=["Asha"],
                                timestamp_start=f"00:0{i}", timestamp_end=f"00:0{i + 1}", embedding=embedding))
        db.commit()
    finally:
        db.close()
    return str(meeting_id)


@pytest.fixture(autouse=True)
def nothing_checked_out_before():
    auth.user_row_cache.clear()
    assert engine.pool.checkedout() == 0, "a previous test leaked a connection"
    yield
    auth.user_row_cache.clear()


# ---------------------------------------------------------------------------
# 3. get_meeting
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_sign(monkeypatch):
    calls = []

    def sign(storage_path, expires_in=86400):
        calls.append({"path": storage_path, "checked_out": engine.pool.checkedout()})
        return f"https://signed.example/{storage_path}"

    monkeypatch.setattr(meetings, "get_signed_recording_url", sign)
    return calls


@pytest.mark.parametrize("cache", ["miss", "hit"])
def test_get_meeting_holds_no_connection_while_signing(fake_sign, monkeypatch, cache):
    user_id = add_user()
    meeting_id = add_meeting(user_id)
    monkeypatch.setattr(auth, "_identify", lambda token: (user_id, f"u-{user_id}@example.com"))
    if cache == "hit":
        auth.user_row_cache.remember(user_id)

    response = TestClient(app).get(f"/meetings/{meeting_id}", headers=HEADERS)

    assert response.status_code == 200, response.text
    # On "miss" the fixture emptied the cache, so this proves auth took its
    # database path during the request rather than skipping it.
    assert auth.user_row_cache.known(user_id)
    body = response.json()
    assert body["id"] == meeting_id and body["status"] == "completed"
    assert body["audio_playback_url"] == f"https://signed.example/{user_id}/{meeting_id}/recording.m4a"
    assert [c["checked_out"] for c in fake_sign] == [0], f"checked out while signing ({cache}): {fake_sign}"


def test_get_meeting_does_not_sign_an_unfinished_meeting(fake_sign, monkeypatch):
    user_id = add_user()
    meeting_id = add_meeting(user_id, status="transcribing")
    monkeypatch.setattr(auth, "_identify", lambda token: (user_id, f"u-{user_id}@example.com"))

    response = TestClient(app).get(f"/meetings/{meeting_id}", headers=HEADERS)

    assert response.status_code == 200
    assert "audio_playback_url" not in response.json()
    assert fake_sign == []


def test_get_meeting_for_another_users_meeting_is_404(fake_sign, monkeypatch):
    meeting_id = add_meeting(add_user())
    stranger = add_user()
    monkeypatch.setattr(auth, "_identify", lambda token: (stranger, f"u-{stranger}@example.com"))

    response = TestClient(app).get(f"/meetings/{meeting_id}", headers=HEADERS)

    assert response.status_code == 404
    assert fake_sign == []
