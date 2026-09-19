"""
Ask AI Phase 5 - the stream route, its rate limit, and custom instructions
on /users/me.

Ownership and the conversation routes are in test_ask_isolation.py and
test_ask_conversations.py.
"""
import json

import pytest

from app.api import ask as ask_api
from app.ask_ai.agent import history, service
from app.config import settings
from app.db.database import engine
from app.services import rate_limit

from tests.ask_ai_fixtures import api  # noqa: F401 - fixture
from tests.fake_ai_providers import FakeGeminiChat, FakeGroq, call, install_fast_sleeps
from tests.test_rate_limit import REDIS_URL, _flush, needs_redis


def sse(body: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def chat_id(api):
    return api.client.post("/ask/conversations", headers=api.me).json()["id"]


@pytest.fixture
def spy_stream(monkeypatch):
    """
    Replaces the service with a fake that records what the route handed it
    and how many pooled connections were checked out while it "answered".
    """
    seen = {"calls": [], "checked_out": []}

    async def fake_ask_stream(question, conversation_id, user_id, tz_name):
        seen["calls"].append({"question": question, "conversation_id": str(conversation_id),
                              "user_id": user_id, "tz_name": tz_name})
        seen["checked_out"].append(engine.pool.checkedout())
        yield {"type": "delta", "text": "Answer."}
        yield {"type": "done", "session_id": str(conversation_id), "tools_used": []}

    monkeypatch.setattr(ask_api, "ask_stream", fake_ask_stream)
    return seen


def stream(api, conversation_id, headers=None, **body):
    body.setdefault("question", "What happened in August?")
    return api.client.post(f"/ask/conversations/{conversation_id}/stream", json=body, headers=headers or api.me)


# ---------------------------------------------------------------------------
# The stream route
# ---------------------------------------------------------------------------

def test_the_stream_carries_the_agents_events_end_to_end(api, chat_id, monkeypatch):
    """The real service and agent loop behind the route, with the providers faked."""
    log = []
    gemini = FakeGeminiChat(log).install(monkeypatch)
    FakeGroq(log).install(monkeypatch)
    install_fast_sleeps(monkeypatch)

    async def fake_title(question, *, user_id):
        return "August recap"

    monkeypatch.setattr(service, "generate_title", fake_title)
    gemini.reply(call("list_meetings", start_date="2026-08-01", end_date="2026-08-31"))
    gemini.reply("No meetings in August.")

    response = stream(api, chat_id, timezone="Asia/Kolkata")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert [e["type"] for e in sse(response.text)] == ["tool", "delta", "title", "done"]
    assert sse(response.text)[2] == {"type": "title", "title": "August recap"}
    listed = api.client.get("/ask/conversations", headers=api.me).json()
    assert [(c["id"], c["title"]) for c in listed] == [(chat_id, "August recap")]


def test_no_connection_is_held_while_the_answer_streams(api, chat_id, spy_stream):
    """
    The route's ownership check uses the request's session; it must be
    released before the stream starts, or every concurrent question would
    hold one of the pool's connections for its whole answer.
    """
    from app.api import auth

    auth.user_row_cache.clear()  # the cache-miss path, where auth also uses the request's session
    assert engine.pool.checkedout() == 0, "a fixture leaked a connection - the measurement would be off"

    response = stream(api, chat_id)

    assert response.status_code == 200
    assert spy_stream["checked_out"] == [0]


def test_the_route_passes_the_question_the_chat_the_user_and_the_timezone(api, chat_id, spy_stream):
    stream(api, chat_id, question="Budget?", timezone="Asia/Kolkata")

    assert spy_stream["calls"] == [{
        "question": "Budget?", "conversation_id": chat_id, "user_id": api.user_id, "tz_name": "Asia/Kolkata",
    }]


@pytest.mark.parametrize("timezone", [None, "", "Not/AZone", "../../etc/passwd"])
def test_a_missing_or_unknown_timezone_becomes_utc(api, chat_id, spy_stream, timezone):
    body = {} if timezone is None else {"timezone": timezone}

    assert stream(api, chat_id, **body).status_code == 200
    assert spy_stream["calls"][0]["tz_name"] == "UTC"


@pytest.mark.parametrize("question", ["", "x" * 4001])
def test_an_empty_or_overlong_question_is_a_422(api, chat_id, spy_stream, question):
    assert stream(api, chat_id, question=question).status_code == 422
    assert spy_stream["calls"] == []


def test_a_failure_inside_the_stream_is_an_error_event(api, chat_id, monkeypatch):
    async def explodes(*args, **kwargs):
        raise RuntimeError("stream exploded")
        yield  # an async generator

    monkeypatch.setattr(ask_api, "ask_stream", explodes)

    response = stream(api, chat_id)

    assert response.status_code == 200
    assert sse(response.text) == [{"type": "error", "message": "Something went wrong generating the answer."}]


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

ASK_LIMIT = 2


@pytest.fixture
def limiter(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "redis_url", REDIS_URL)
    monkeypatch.setattr(settings, "ask_ai_rate_limit_requests", ASK_LIMIT)
    monkeypatch.setattr(settings, "ask_ai_rate_limit_window_seconds", 60)
    rate_limit.reset_clients()
    _flush()
    yield
    _flush()
    rate_limit.reset_clients()


@needs_redis
def test_the_question_after_the_limit_is_refused_before_the_stream_starts(api, chat_id, spy_stream, limiter):
    for i in range(ASK_LIMIT):
        assert stream(api, chat_id).status_code == 200, f"question {i + 1}"

    refused = stream(api, chat_id)

    assert refused.status_code == 429
    assert refused.json()["detail"].startswith("You've asked a lot of questions in a short time.")
    assert refused.headers["Retry-After"].isdigit()
    assert len(spy_stream["calls"]) == ASK_LIMIT


@needs_redis
def test_ask_ai_has_its_own_budget_separate_from_meeting_chat(api, chat_id, spy_stream, limiter):
    for _ in range(ASK_LIMIT):
        stream(api, chat_id)
    assert stream(api, chat_id).status_code == 429

    # Exhausting Ask AI spent nothing of the per-meeting chat's counter.
    import redis

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        keys = client.keys(rate_limit.KEY_PREFIX + "*")
    finally:
        client.close()
    assert keys == [f"{rate_limit.KEY_PREFIX}ask-ai:{api.user_id}"]


@needs_redis
def test_managing_chats_is_not_rate_limited(api, limiter):
    for _ in range(ASK_LIMIT + 3):
        assert api.client.post("/ask/conversations", headers=api.me).status_code == 200
        assert api.client.get("/ask/conversations", headers=api.me).status_code == 200


# ---------------------------------------------------------------------------
# Custom instructions on /users/me
# ---------------------------------------------------------------------------

def test_settings_include_ask_ai_instructions(api):
    assert api.client.get("/users/me", headers=api.me).json()["ask_ai_instructions"] is None


def test_instructions_can_be_saved_alone_without_touching_the_bot_name(api):
    before = api.client.get("/users/me", headers=api.me).json()["bot_display_name"]

    response = api.client.patch("/users/me", json={"ask_ai_instructions": "  I lead the platform team.  "}, headers=api.me)

    assert response.status_code == 200
    assert response.json()["ask_ai_instructions"] == "I lead the platform team."
    assert response.json()["bot_display_name"] == before


def test_the_bot_name_can_still_be_saved_alone_without_touching_instructions(api):
    api.client.patch("/users/me", json={"ask_ai_instructions": "Answer briefly."}, headers=api.me)

    response = api.client.patch("/users/me", json={"bot_display_name": "Notes Bot"}, headers=api.me)

    assert response.json()["bot_display_name"] == "Notes Bot"
    assert response.json()["ask_ai_instructions"] == "Answer briefly."


@pytest.mark.parametrize("cleared", ["", "   ", None])
def test_blank_or_null_instructions_clear_them(api, cleared):
    api.client.patch("/users/me", json={"ask_ai_instructions": "Answer briefly."}, headers=api.me)

    response = api.client.patch("/users/me", json={"ask_ai_instructions": cleared}, headers=api.me)

    assert response.json()["ask_ai_instructions"] is None


def test_instructions_over_1000_characters_are_a_422(api):
    assert api.client.patch("/users/me", json={"ask_ai_instructions": "x" * 1001}, headers=api.me).status_code == 422
    assert api.client.patch("/users/me", json={"ask_ai_instructions": "x" * 1000}, headers=api.me).status_code == 200


def test_a_null_bot_name_is_a_422(api):
    assert api.client.patch("/users/me", json={"bot_display_name": None}, headers=api.me).status_code == 422


def test_instructions_are_per_user(api):
    api.client.patch("/users/me", json={"ask_ai_instructions": "Mine."}, headers=api.me)

    assert api.client.get("/users/me", headers=api.other).json()["ask_ai_instructions"] is None
