"""
Chat must not hold a pooled connection while the model is answering.

Both chat routes run their ownership/status check through the request's
session and then spend seconds in the LLM. Until that session is released, the
connection it checked out stays out for the whole answer - and for the
streaming route, FastAPI 0.141 runs get_db's cleanup only after the last chunk.
With the backend's pool at 8 (db_pool_size), eight concurrent chats would take
all of it.

The LLM call is replaced by a fake that reads engine.pool.checkedout() while it
"generates", which measures exactly the thing and costs nothing.

get_current_user is deliberately NOT overridden. It shares the request's
session with the route, and on a user-row cache miss _ensure_user_row runs a
SELECT and returns without committing - a second way to hold the same
connection. Only _identify (the JWT check) is stubbed, so the database half of
auth runs for real, and the cache is cleared or warmed per test.

Postgres is required.
"""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import auth, chat
from app.db.database import SessionLocal, engine
from app.db.models import Meeting, User
from app.main import app

HEADERS = {"Authorization": "Bearer stub-token-identity-is-stubbed"}


@pytest.fixture
def owner(monkeypatch):
    """A user and a completed meeting, written and released before the test."""
    user_id, meeting_id = uuid.uuid4(), uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(User(id=user_id, email=f"chat-{user_id}@example.com"))
        db.add(Meeting(id=meeting_id, user_id=user_id, meeting_url="https://zoom.us/j/1",
                       platform="zoom", status="completed", title="Planning"))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(auth, "_identify", lambda token: (str(user_id), f"chat-{user_id}@example.com"))
    auth.user_row_cache.clear()
    yield {"user_id": str(user_id), "meeting_id": str(meeting_id)}
    auth.user_row_cache.clear()


@pytest.fixture
def llm(monkeypatch):
    """Fake ask_question / ask_question_stream that record checkedout() mid-answer."""
    seen = {"calls": 0, "checked_out": []}

    async def _generate():
        seen["calls"] += 1
        await asyncio.sleep(0.05)  # "the model is thinking"
        seen["checked_out"].append(engine.pool.checkedout())

    async def fake_ask_question(meeting_id, question, session_id=None, user_id=None):
        await _generate()
        return {"answer": "stub answer", "session_id": "s1", "tools_used": []}

    async def fake_ask_question_stream(meeting_id, question, session_id=None, user_id=None):
        yield {"type": "delta", "text": "stub "}
        await _generate()
        yield {"type": "delta", "text": "answer"}
        await _generate()
        yield {"type": "done", "session_id": "s1", "tools_used": []}

    monkeypatch.setattr(chat, "ask_question", fake_ask_question)
    monkeypatch.setattr(chat, "ask_question_stream", fake_ask_question_stream)
    return seen


ROUTES = {
    "chat": lambda mid: f"/meetings/{mid}/chat",
    "stream": lambda mid: f"/meetings/{mid}/chat/stream",
}


@pytest.mark.parametrize("route", ["chat", "stream"])
@pytest.mark.parametrize("cache", ["miss", "hit"])
def test_no_connection_is_held_while_the_model_answers(owner, llm, route, cache):
    if cache == "hit":
        auth.user_row_cache.remember(owner["user_id"])
    else:
        assert len(auth.user_row_cache) == 0
    assert engine.pool.checkedout() == 0, "a fixture leaked a connection - the measurement would be off"

    response = TestClient(app).post(
        ROUTES[route](owner["meeting_id"]), json={"question": "What was decided?"}, headers=HEADERS
    )

    assert response.status_code == 200, response.text
    if route == "stream":
        assert '"type": "done"' in response.text, "the stream did not run to completion"
    assert llm["calls"] >= 1
    # The cache-miss variant is only worth anything if auth really took the
    # database path: an empty cache before, a remembered user after.
    assert auth.user_row_cache.known(owner["user_id"])
    assert llm["checked_out"] == [0] * llm["calls"], (
        f"connections checked out during the LLM call: {llm['checked_out']} "
        f"(route={route}, cache={cache})"
    )


@pytest.mark.parametrize("route", ["chat", "stream"])
def test_a_meeting_you_do_not_own_is_a_404_before_any_answer(owner, llm, route):
    response = TestClient(app).post(ROUTES[route](uuid.uuid4()), json={"question": "hi"}, headers=HEADERS)

    assert response.status_code == 404
    assert response.json() == {"detail": "Meeting not found"}
    assert llm["calls"] == 0


@pytest.mark.parametrize("route", ["chat", "stream"])
def test_a_meeting_that_is_not_completed_is_a_409_before_any_answer(owner, llm, route):
    db = SessionLocal()
    try:
        db.query(Meeting).filter(Meeting.id == owner["meeting_id"]).update({"status": "transcribing"})
        db.commit()
    finally:
        db.close()

    response = TestClient(app).post(ROUTES[route](owner["meeting_id"]), json={"question": "hi"}, headers=HEADERS)

    assert response.status_code == 409
    assert "isn't ready yet" in response.json()["detail"]
    assert llm["calls"] == 0
