"""
B3 item 3 - the chat fallback ladder: Gemini -> Groq.

ask_question_stream tries Gemini's streaming call chat_max_retries times. Once
those are spent it hands the question to Groq - but only if the last error is
transient (gemini_errors.is_transient: 429, 503, 504, httpx connection errors)
and a Groq key is configured. Otherwise the user gets a canned message, nothing
is stored, and the question is rolled out of the session.

Driven through the real generator, events and all. Gemini is faked at
client.aio.models.generate_content_stream; Groq at HTTP, under the real
chat_fallback_groq module (see tests/fake_ai_providers.py). The tools run for
real against the meeting row. Postgres only - chat_messages is a real table.

The reset/storage semantics of the same code path are item 4, in
test_chat_reset_and_saving.py; this file is about which provider is called,
in what order, and what it costs when it should not be.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import ChatMessage, Meeting
from app.rag import chat_service

from tests.fake_ai_providers import NON_TRANSIENT, TRANSIENT, FakeGeminiChat, FakeGroq, install_fast_sleeps

HIGH_LOAD = "The AI service is currently experiencing high load or rate limits. Please try again in a few moments."

TRANSCRIPT = {
    "title": "Launch review",
    "summary": "The team confirmed the launch for the 14th.",
    "key_points": ["QA signed off"],
    "action_items": [{"item": "Finish the API work", "owner": "Rohan", "timestamp": "00:05"}],
    "conclusion": "Launch on the 14th.",
    "conversation": [],
}


@pytest.fixture
def chat(monkeypatch, db, user):
    """A completed meeting, both providers faked, retries pinned to 2, sleeps skipped."""
    log = []
    monkeypatch.setattr(settings, "chat_max_retries", 2)
    monkeypatch.setattr(settings, "groq_api_key", "stub-groq-key")
    meeting = Meeting(
        id=uuid.uuid4(), user_id=user.id, meeting_url="https://zoom.us/j/1", platform="zoom",
        status="completed", title="Launch review", transcript=TRANSCRIPT, embedding_provider="gemini",
    )
    db.add(meeting)
    db.commit()
    return SimpleNamespace(
        log=log,
        gemini=FakeGeminiChat(log).install(monkeypatch),
        groq=FakeGroq(log).install(monkeypatch),
        slept=install_fast_sleeps(monkeypatch),
        meeting_id=str(meeting.id),
        user_id=str(user.id),
    )


def ask(chat, *questions):
    """
    Asks each question in turn on one event loop and session, the way one
    browser tab would. Returns one event list per question, and logs every
    event into chat.log beside the provider calls.
    """
    async def run():
        transcripts = []
        for question in questions:
            events = []
            chat.log.append(f"ask:{question}")
            async for event in chat_service.ask_question_stream(chat.meeting_id, question, None, chat.user_id):
                events.append(event)
                chat.log.append(f"event:{event['type']}")
            transcripts.append(events)
        return transcripts

    return asyncio.run(run())


def providers(log):
    return [e for e in log if e.startswith(("gemini:", "groq:"))]


def shown_answer(events):
    """What the user ends up looking at: every delta since the last reset."""
    text = []
    for event in events:
        if event["type"] == "reset":
            text.clear()
        elif event["type"] == "delta":
            text.append(event["text"])
    return "".join(text)


def stored(chat):
    other = SessionLocal()
    try:
        rows = (
            other.query(ChatMessage)
            .filter(ChatMessage.meeting_id == chat.meeting_id)
            .order_by(ChatMessage.id)
            .all()
        )
        return [(r.role, r.content, r.tools_used) for r in rows]
    finally:
        other.close()


def session_history(chat):
    """The in-memory session the next question is built on, as (role, text)."""
    key = f"{chat.user_id}:{chat.meeting_id}:meeting-{chat.meeting_id}"
    entry = chat_service._session_cache.cache.get(key)
    if entry is None:
        return None
    return [
        (c.role, "".join(p.text or "" for p in (c.parts or []) if getattr(p, "text", None)))
        for c in entry[1]
    ]


# ---------------------------------------------------------------------------
# Transient: Groq answers once Gemini's retries are spent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("failure", list(TRANSIENT))
def test_a_transient_gemini_failure_falls_back_to_groq_after_its_retries(chat, failure):
    chat.gemini.fail(TRANSIENT[failure], times=2)
    chat.groq.answer("The launch is ", "on the 14th.")

    [events] = ask(chat, "When is the launch?")

    assert providers(chat.log) == ["gemini:1", "gemini:2", "groq:1"]
    assert len(chat.slept) == 1, "one backoff between Gemini's two attempts, none before Groq"
    assert [e["type"] for e in events] == ["delta", "delta", "done"]
    assert shown_answer(events) == "The launch is on the 14th."
    request = chat.groq.requests[0]
    assert request["model"] == settings.groq_chat_model
    assert request["messages"][0]["role"] == "system"
    assert request["messages"][-1] == {"role": "user", "content": "When is the launch?"}
    assert stored(chat) == [
        ("user", "When is the launch?", None),
        ("assistant", "The launch is on the 14th.", None),
    ]


def test_a_failure_that_clears_within_gemini_retries_never_reaches_groq(chat):
    chat.gemini.fail(TRANSIENT["504"]).reply("The launch is on the 14th.")

    [events] = ask(chat, "When is the launch?")

    assert providers(chat.log) == ["gemini:1", "gemini:2"]
    assert chat.groq.calls == 0
    assert shown_answer(events) == "The launch is on the 14th."
    assert [row[:2] for row in stored(chat)] == [("user", "When is the launch?"), ("assistant", "The launch is on the 14th.")]


# ---------------------------------------------------------------------------
# Non-transient: no Groq call, a clean failure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("failure", list(NON_TRANSIENT))
def test_a_non_transient_gemini_error_does_not_fall_back_to_groq(chat, failure):
    """
    No Groq request, no stored turn, the question rolled out of the session.
    Pinned as it behaves: the chat loop retries every APIError, including a
    400, before deciding - unlike the transcription and embedding ladders,
    which raise a non-transient error on the first attempt. (Recorded in
    docs/scaling-plan.md B3, along with the wording of the message below.)
    """
    chat.gemini.fail(NON_TRANSIENT[failure], times=2)

    [events] = ask(chat, "When is the launch?")

    assert providers(chat.log) == ["gemini:1", "gemini:2"]
    assert chat.groq.calls == 0
    assert events == [{"type": "delta", "text": HIGH_LOAD}, {"type": "done", "session_id": f"meeting-{chat.meeting_id}", "tools_used": []}]
    assert stored(chat) == []
    assert session_history(chat) == []


# ---------------------------------------------------------------------------
# Fallback not configured
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("failure", ["429", "503", "504", "connect-error"])
def test_with_no_groq_key_a_transient_failure_ends_cleanly_without_calling_groq(chat, monkeypatch, failure):
    monkeypatch.setattr(settings, "groq_api_key", "")
    chat.gemini.fail(TRANSIENT[failure], times=2)

    [events] = ask(chat, "When is the launch?")

    assert providers(chat.log) == ["gemini:1", "gemini:2"]
    assert [e["type"] for e in events] == ["delta", "done"], "an exception or an error event instead of a clean end"
    assert shown_answer(events) == HIGH_LOAD
    assert stored(chat) == []
    assert session_history(chat) == []


# ---------------------------------------------------------------------------
# Groq's own rung
# ---------------------------------------------------------------------------

def test_groq_retries_its_own_transient_errors_before_answering(chat):
    chat.gemini.fail(TRANSIENT["503"], times=2)
    chat.groq.status(503).raise_(TRANSIENT["connect-error"]).answer("The launch is on the 14th.")

    [events] = ask(chat, "When is the launch?")

    assert providers(chat.log) == ["gemini:1", "gemini:2", "groq:1", "groq:2", "groq:3"]
    assert shown_answer(events) == "The launch is on the 14th."
    assert len(stored(chat)) == 2


def test_groq_does_not_retry_a_400(chat):
    chat.gemini.fail(TRANSIENT["503"], times=2)
    chat.groq.status(400)

    [events] = ask(chat, "When is the launch?")

    assert providers(chat.log) == ["gemini:1", "gemini:2", "groq:1"]
    assert shown_answer(events) == HIGH_LOAD
    assert stored(chat) == []
