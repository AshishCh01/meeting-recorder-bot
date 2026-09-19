"""
A warm chat session's history is trimmed to its last N entries by count, and
the cut must never leave it opening mid tool round-trip.

A question that uses tools takes several history entries - the question, then
a function call and a function response per tool round, then the answer - so
cutting at a fixed count can land between a call and its response. Gemini
rejects such a history ("400 INVALID_ARGUMENT: Please ensure that function
call turn comes immediately after a user turn or after a function response
turn"), and because the cut happens before the request's rollback mark, every
later question in the session failed the same way. With two tool rounds per
question that was the 5th question.

The fake Gemini here is strict the way the real one is: it refuses a history
that doesn't open on a question the user typed. Both chats share the loop
(rag/agent_runner.py), so both are covered.
"""
import pytest

from app.rag import agent_runner

from tests.fake_ai_providers import NON_TRANSIENT, call
from tests.test_ask_stream import ask as ask_ai_questions
from tests.test_ask_stream import ask_ai  # noqa: F401 - fixture
from tests.test_chat_fallback_ladder import ask as ask_meeting_questions
from tests.test_chat_fallback_ladder import chat  # noqa: F401 - fixture

HIGH_LOAD = "The AI service is currently experiencing high load or rate limits. Please try again in a few moments."
QUESTIONS = 8


def opens_on_a_question(contents) -> bool:
    first = contents[0]
    parts = first.parts or []
    return (
        first.role == "user"
        and any(getattr(p, "text", None) for p in parts)
        and not any(getattr(p, "function_response", None) for p in parts)
    )


@pytest.fixture
def strict_gemini(monkeypatch):
    """
    Wraps whichever fake is installed so a history that opens mid round-trip
    fails with the 400 the real API returns, and records every history sent.
    """
    sent = []

    def install():
        inner = agent_runner.client.aio.models.generate_content_stream

        async def strict(model, contents, config):
            sent.append(list(contents))
            if not opens_on_a_question(contents):
                raise NON_TRANSIENT["400"]()
            return await inner(model=model, contents=contents, config=config)

        monkeypatch.setattr(agent_runner.client.aio.models, "generate_content_stream", strict)
        return sent

    return install


def answers(transcripts):
    return ["".join(e["text"] for e in events if e["type"] == "delta") for events in transcripts]


def test_a_long_meeting_chat_with_two_tool_rounds_per_question_keeps_answering(chat, strict_gemini):
    for n in range(1, QUESTIONS + 1):
        chat.gemini.reply(call("_get_meeting_summary"))
        chat.gemini.reply(call("_get_action_items"))
        chat.gemini.reply(f"Answer {n}.")
    sent = strict_gemini()

    transcripts = ask_meeting_questions(chat, *[f"Question {n}?" for n in range(1, QUESTIONS + 1)])

    assert answers(transcripts) == [f"Answer {n}." for n in range(1, QUESTIONS + 1)]
    assert HIGH_LOAD not in answers(transcripts)
    assert all(opens_on_a_question(contents) for contents in sent)


def test_a_long_ask_ai_chat_with_two_tool_rounds_per_question_keeps_answering(ask_ai, strict_gemini):
    for n in range(1, QUESTIONS + 1):
        ask_ai.gemini.reply(call("list_meetings", start_date="2026-08-01", end_date="2026-08-31"))
        ask_ai.gemini.reply(call("list_meetings", title_query="review"))
        ask_ai.gemini.reply(f"Answer {n}.")
    sent = strict_gemini()

    transcripts = ask_ai_questions(ask_ai, *[f"Question {n}?" for n in range(1, QUESTIONS + 1)])

    assert answers(transcripts) == [f"Answer {n}." for n in range(1, QUESTIONS + 1)]
    assert all(opens_on_a_question(contents) for contents in sent)


def test_the_trim_keeps_as_much_recent_context_as_it_can(ask_ai, strict_gemini):
    """Only the broken fragment is dropped: the last question still sees the previous exchanges."""
    for n in range(1, QUESTIONS + 1):
        ask_ai.gemini.reply(call("list_meetings"))
        ask_ai.gemini.reply(call("list_meetings", title_query="review"))
        ask_ai.gemini.reply(f"Answer {n}.")
    sent = strict_gemini()

    ask_ai_questions(ask_ai, *[f"Question {n}?" for n in range(1, QUESTIONS + 1)])

    last_request = [(c.role, "".join(p.text or "" for p in (c.parts or []) if getattr(p, "text", None))) for c in sent[-1]]
    assert last_request[0] == ("user", "Question 5?"), "trimmed further back than the cut required"
    assert ("model", "Answer 7.") in last_request
    assert ("user", "Question 8?") in last_request
