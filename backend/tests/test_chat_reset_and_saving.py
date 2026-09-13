"""
B3 item 4 - chat_service's reset and storage semantics.

What a user sees in the chat panel is every "delta" since the last "reset"
(frontend/src/hooks/useMeetingChat.js clears the bubble on reset). So when a
stream breaks after some of the answer has already been sent, the service
must reset *before* anything else writes into that bubble - a retry or Groq -
or the final answer is printed after half of itself. And what is stored in
chat_messages is replayed into every later cold session, so a turn that never
produced an answer must store nothing and leave nothing in the session.

Tested at the event level, not only on the final answer: the order of events
against provider calls is the property.

Uses test_chat_fallback_ladder.py's harness (the same faked providers, the
real generator, real chat_messages). Postgres only.
"""
import asyncio

from app.rag import chat_service

from tests.fake_ai_providers import TRANSIENT, call
from tests.test_chat_fallback_ladder import HIGH_LOAD, ask, chat, session_history, shown_answer, stored  # noqa: F401 - chat is a fixture


def types_of(events):
    return [e["type"] for e in events]


def between(log, first, last):
    """The slice of the shared log from `first` up to and including `last`."""
    start = log.index(first)
    return log[start:log.index(last, start) + 1]


# ---------------------------------------------------------------------------
# 1. A stream that breaks mid-answer
# ---------------------------------------------------------------------------

def test_a_mid_answer_break_resets_before_the_retry_and_the_retry_replaces_the_partial(chat):
    chat.gemini.reply("The launch was moved ", TRANSIENT["dropped-connection"]())
    chat.gemini.reply("The launch is on the 14th.")

    [events] = ask(chat, "When is the launch?")

    assert types_of(events) == ["delta", "reset", "delta", "done"]
    # The reset reaches the client before the retry even starts.
    assert between(chat.log, "gemini:1", "gemini:2") == ["gemini:1", "event:delta", "event:reset", "gemini:2"]
    assert shown_answer(events) == "The launch is on the 14th."
    assert stored(chat) == [
        ("user", "When is the launch?", None),
        ("assistant", "The launch is on the 14th.", None),
    ], "the stored answer carries the abandoned fragment"


def test_a_mid_answer_break_resets_before_the_groq_fallback_writes(chat):
    chat.gemini.reply("The launch was moved ", TRANSIENT["503"]())
    chat.gemini.fail(TRANSIENT["503"])
    chat.groq.answer("The launch is ", "on the 14th.")

    [events] = ask(chat, "When is the launch?")

    assert types_of(events) == ["delta", "reset", "delta", "delta", "done"]
    assert between(chat.log, "gemini:1", "groq:1") == ["gemini:1", "event:delta", "event:reset", "gemini:2", "groq:1"]
    assert shown_answer(events) == "The launch is on the 14th."
    assert stored(chat)[1] == ("assistant", "The launch is on the 14th.", None)


def test_a_reset_also_clears_preamble_from_an_earlier_tool_turn(chat):
    """
    "Let me check..." was streamed on the first turn, before a tool call; the
    answer turn then breaks. The reset clears the whole reply bubble, so the
    preamble must not reappear in what is stored either.
    """
    chat.gemini.reply("Let me check the action items. ", call("_get_action_items"))
    chat.gemini.reply("Rohan owns ", TRANSIENT["timeout"]())
    chat.gemini.reply("Rohan owns the API work.")

    [events] = ask(chat, "Who owns the API work?")

    assert types_of(events) == ["delta", "tool", "delta", "reset", "delta", "done"]
    assert shown_answer(events) == "Rohan owns the API work."
    assert stored(chat) == [
        ("user", "Who owns the API work?", None),
        ("assistant", "Rohan owns the API work.", ["get_action_items"]),
    ]


def test_the_non_streaming_wrapper_returns_only_the_final_answer(chat):
    chat.gemini.reply("The launch was moved ", TRANSIENT["dropped-connection"]())
    chat.gemini.reply("The launch is on the 14th.")

    result = asyncio.run(chat_service.ask_question(chat.meeting_id, "When is the launch?", None, chat.user_id))

    assert result["answer"] == "The launch is on the 14th."


# ---------------------------------------------------------------------------
# 2. Gemini and Groq both fail
# ---------------------------------------------------------------------------

def test_when_gemini_and_groq_both_fail_nothing_is_stored_and_the_next_question_starts_clean(chat):
    chat.gemini.reply("Half an ", TRANSIENT["503"]())
    chat.gemini.fail(TRANSIENT["503"])
    chat.groq.status(503, times=3)                 # Groq's own three attempts
    chat.gemini.reply("The launch is on the 14th.")  # the next question

    failed, answered = ask(chat, "What went wrong at the launch?", "When is the launch?")

    assert types_of(failed) == ["delta", "reset", "delta", "done"]
    assert shown_answer(failed) == HIGH_LOAD
    assert chat.groq.calls == 3

    # The next question's prompt is built on a session that never saw the
    # failed one - not its question, not its half-answer.
    assert chat.gemini.contents_seen[-1] == [("user", "When is the launch?")]
    assert shown_answer(answered) == "The launch is on the 14th."
    assert stored(chat) == [
        ("user", "When is the launch?", None),
        ("assistant", "The launch is on the 14th.", None),
    ], "the failed turn was stored"
    assert session_history(chat) == [("user", "When is the launch?"), ("model", "The launch is on the 14th.")]


def test_a_failed_turn_leaves_no_row_and_no_session_entry(chat):
    """The same failure, checked at the moment it ends - before any later question."""
    chat.gemini.fail(TRANSIENT["429"], times=2)
    chat.groq.raise_(TRANSIENT["connect-error"], times=3)

    [events] = ask(chat, "What went wrong at the launch?")

    assert types_of(events) == ["delta", "done"]
    assert stored(chat) == []
    assert session_history(chat) == []


def test_groq_dying_mid_answer_resets_its_half_answer_and_stores_nothing(chat):
    chat.gemini.fail(TRANSIENT["503"], times=2)
    chat.groq.answer_then_drop("The launch was ", "moved to ")

    [events] = ask(chat, "When is the launch?")

    assert types_of(events) == ["delta", "delta", "reset", "delta", "done"]
    assert shown_answer(events) == HIGH_LOAD
    assert chat.groq.calls == 1, "Groq must not retry a turn that already streamed text"
    assert stored(chat) == []
    assert session_history(chat) == []


# ---------------------------------------------------------------------------
# 3. Groq answers
# ---------------------------------------------------------------------------

def test_a_groq_answer_is_stored_with_the_tools_groq_actually_used(chat):
    chat.gemini.fail(TRANSIENT["503"], times=2)
    chat.groq.tool_calls("get_action_items").answer("Rohan owns the API work.")

    [events] = ask(chat, "Who owns the API work?")

    assert types_of(events) == ["tool", "delta", "done"]
    assert events[0] == {"type": "tool", "name": "get_action_items"}
    assert events[-1]["tools_used"] == ["get_action_items"]
    # The tool really ran: its result went back to Groq on the second request.
    tool_messages = [m for m in chat.groq.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1 and "Finish the API work" in tool_messages[0]["content"]
    assert stored(chat) == [
        ("user", "Who owns the API work?", None),
        ("assistant", "Rohan owns the API work.", ["get_action_items"]),
    ]
    assert session_history(chat)[-1] == ("model", "Rohan owns the API work.")


def test_a_groq_answer_that_used_no_tools_stores_none(chat):
    chat.gemini.fail(TRANSIENT["504"], times=2)
    chat.groq.answer("The launch is on the 14th.")

    ask(chat, "When is the launch?")

    assert stored(chat)[1] == ("assistant", "The launch is on the 14th.", None)
