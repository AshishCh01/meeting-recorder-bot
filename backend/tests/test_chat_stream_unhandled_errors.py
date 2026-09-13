"""
A network error the chat retry handler did not name must not escape the stream.

ask_question_stream retried and reset only on errors.APIError, ConnectError,
TimeoutException and RemoteProtocolError. httpx.ReadError - "connection reset
by peer" while the answer is streaming - is none of those, so before this fix:

  - it escaped the generator mid-answer: no reset, no retry, and the route
    replaced the half-written reply with a generic error;
  - _rollback_history never ran, so the failed question stayed in the
    in-memory session and the next question was sent to Gemini behind it,
    two user turns in a row.

The fix, in chat_service only:

  1. The retry handler catches httpx.NetworkError (ConnectError, ReadError,
     WriteError, CloseError) rather than ConnectError alone, so a dropped read
     gets the same reset -> retry -> canned-message path as the others.
  2. Any other exception from the Gemini call still propagates - the route
     already turns it into an error event - but rolls the question out of
     the session first.

Deliberately not changed: gemini_errors.is_transient. A ReadError is now
retried, but does not fall back to Groq, because changing the shared predicate
would change the transcription and embedding ladders too.

Uses test_chat_fallback_ladder.py's harness. Postgres only.
"""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import auth
from app.main import app
from app.rag import chat_service

from tests.fake_ai_providers import TRANSIENT
from tests.test_chat_fallback_ladder import HIGH_LOAD, ask, chat, session_history, shown_answer, stored  # noqa: F401 - chat is a fixture

NETWORK_ERRORS = {
    "read-error": lambda: httpx.ReadError("[Errno 104] Connection reset by peer"),
    "write-error": lambda: httpx.WriteError("[Errno 32] Broken pipe"),
}


def types_of(events):
    return [e["type"] for e in events]


# ---------------------------------------------------------------------------
# 1. A dropped read or write is retried like any other network failure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("failure", list(NETWORK_ERRORS))
def test_a_connection_reset_mid_answer_resets_and_retries(chat, failure):
    chat.gemini.reply("The launch was moved ", NETWORK_ERRORS[failure]())
    chat.gemini.reply("The launch is on the 14th.")

    [events] = ask(chat, "When is the launch?")

    assert types_of(events) == ["delta", "reset", "delta", "done"]
    assert [e for e in chat.log if e.startswith("gemini:")] == ["gemini:1", "gemini:2"]
    assert shown_answer(events) == "The launch is on the 14th."
    assert stored(chat) == [
        ("user", "When is the launch?", None),
        ("assistant", "The launch is on the 14th.", None),
    ]
    assert session_history(chat) == [("user", "When is the launch?"), ("model", "The launch is on the 14th.")]


def test_a_connection_reset_before_any_text_is_retried_without_a_reset(chat):
    chat.gemini.fail(NETWORK_ERRORS["read-error"]).reply("The launch is on the 14th.")

    [events] = ask(chat, "When is the launch?")

    assert types_of(events) == ["delta", "done"]
    assert chat.gemini.calls == 2
    assert shown_answer(events) == "The launch is on the 14th."


def test_a_persistent_connection_reset_ends_cleanly_without_groq(chat):
    """
    Every attempt resets. The turn ends with the canned message, stores nothing
    and leaves nothing in the session. No Groq request: ReadError is not in
    gemini_errors.is_transient, which this fix deliberately leaves alone.
    """
    chat.gemini.reply("Half an ", NETWORK_ERRORS["read-error"]())
    chat.gemini.fail(NETWORK_ERRORS["read-error"])
    chat.gemini.reply("The launch is on the 14th.")  # the next question

    failed, answered = ask(chat, "What went wrong?", "When is the launch?")

    assert types_of(failed) == ["delta", "reset", "delta", "done"]
    assert shown_answer(failed) == HIGH_LOAD
    assert chat.groq.calls == 0
    assert chat.gemini.contents_seen[-1] == [("user", "When is the launch?")], "the failed question leaked into the next prompt"
    assert shown_answer(answered) == "The launch is on the 14th."
    assert stored(chat) == [
        ("user", "When is the launch?", None),
        ("assistant", "The launch is on the 14th.", None),
    ]


# ---------------------------------------------------------------------------
# 2. Anything else still propagates, but never leaves the question behind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("where", ["mid-answer", "before-any-text"])
def test_an_unexpected_error_propagates_but_rolls_the_question_out_of_the_session(chat, where):
    boom = ValueError("unexpected response shape from the SDK")
    if where == "mid-answer":
        chat.gemini.reply("The launch was ", boom)
    else:
        chat.gemini.fail(lambda: boom)
    chat.gemini.reply("The launch is on the 14th.")  # the next question

    async def run():
        events = []
        with pytest.raises(ValueError, match="unexpected response shape"):
            async for event in chat_service.ask_question_stream(chat.meeting_id, "What went wrong?", None, chat.user_id):
                events.append(event)
        after_failure = session_history(chat)
        async for _ in chat_service.ask_question_stream(chat.meeting_id, "When is the launch?", None, chat.user_id):
            pass
        return events, after_failure

    events, after_failure = asyncio.run(run())

    assert chat.gemini.calls == 2, "an unexpected error must not be retried as if it were transient"
    assert chat.groq.calls == 0
    assert after_failure == [], "the failed question stayed in the session"
    assert chat.gemini.contents_seen[-1] == [("user", "When is the launch?")], "the failed question leaked into the next prompt"
    assert stored(chat) == [
        ("user", "When is the launch?", None),
        ("assistant", "The launch is on the 14th.", None),
    ]


# ---------------------------------------------------------------------------
# 3. Through the real streaming route
# ---------------------------------------------------------------------------

@pytest.fixture
def logged_in(chat, monkeypatch):
    """The JWT check stubbed to the harness's user; the rest of auth runs for real."""
    monkeypatch.setattr(auth, "_identify", lambda token: (chat.user_id, "backend-test@example.com"))
    auth.user_row_cache.clear()
    yield {"Authorization": "Bearer stub-token-identity-is-stubbed"}
    auth.user_row_cache.clear()


def sse_events(body: str):
    import json
    return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]


def test_the_stream_route_shows_a_reset_and_the_retried_answer_not_a_generic_error(chat, logged_in):
    chat.gemini.reply("The launch was moved ", NETWORK_ERRORS["read-error"]())
    chat.gemini.reply("The launch is on the 14th.")

    response = TestClient(app).post(
        f"/meetings/{chat.meeting_id}/chat/stream", json={"question": "When is the launch?"}, headers=logged_in,
    )

    assert response.status_code == 200
    events = sse_events(response.text)
    assert types_of(events) == ["delta", "reset", "delta", "done"], "the route fell back to its generic error"
    assert shown_answer(events) == "The launch is on the 14th."
