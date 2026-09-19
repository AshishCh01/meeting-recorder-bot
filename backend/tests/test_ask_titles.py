"""
Ask AI Phase 4 - naming a chat from its first question.

Gemini, then Groq, then the question itself: a title is cosmetic, so
generate_title never fails. Usage is recorded as ask_ai_title.
"""
import asyncio

import pytest

from app.ask_ai.agent import titles
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import AiUsageEvent

from tests.ask_ai_fixtures import FakeGeminiTitle
from tests.fake_ai_providers import TRANSIENT, FakeGroq, install_fast_sleeps

QUESTION = "What was discussed across all of our meetings in August about pricing?"


@pytest.fixture
def providers(monkeypatch):
    log = []
    monkeypatch.setattr(settings, "groq_api_key", "stub-groq-key")
    install_fast_sleeps(monkeypatch)
    return {
        "gemini": FakeGeminiTitle().install(monkeypatch),
        "groq": FakeGroq(log).install(monkeypatch),
    }


def title_for(question, user_id):
    return asyncio.run(titles.generate_title(question, user_id=str(user_id)))


def usage_rows():
    db = SessionLocal()
    try:
        return db.query(AiUsageEvent).order_by(AiUsageEvent.id).all()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, cleaned", [
    ("August pricing discussions", "August pricing discussions"),
    ('"August pricing discussions."', "August pricing discussions"),
    ("Title: **August pricing**\n\nThis title reflects...", "August pricing"),
    ("  \n  “Q3 budget review”  ", "Q3 budget review"),
    ("# Hiring   plans\tfor Q4", "Hiring plans for Q4"),
])
def test_quotes_labels_markdown_and_newlines_are_stripped(raw, cleaned):
    assert titles.clean_title(raw) == cleaned


def test_a_long_title_is_capped_at_60_characters():
    cleaned = titles.clean_title("word " * 30)
    assert len(cleaned) <= 60 and cleaned.endswith("…")


@pytest.mark.parametrize("raw", [None, "", "   ", '""', "\n\n", "**"])
def test_nothing_usable_is_none(raw):
    assert titles.clean_title(raw) is None


def test_the_fallback_is_the_question_cut_to_50_characters():
    assert titles.fallback_title("Short   question?") == "Short question?"
    fallback = titles.fallback_title(QUESTION)
    assert len(fallback) <= 50 and fallback.endswith("…")
    assert fallback.startswith("What was discussed across all of our meetings")
    assert titles.fallback_title("   ") == "New chat"


# ---------------------------------------------------------------------------
# The provider ladder
# ---------------------------------------------------------------------------

def test_gemini_names_the_chat(user, providers):
    providers["gemini"].reply('"August pricing discussions"', usage=(30, 6))

    assert title_for(QUESTION, user.id) == "August pricing discussions"
    assert QUESTION in providers["gemini"].prompts[0]
    assert providers["groq"].calls == 0

    [row] = usage_rows()
    assert (row.operation, row.provider, row.outcome) == ("ask_ai_title", "gemini", "ok")
    assert (row.input_tokens, row.output_tokens, row.user_id) == (30, 6, user.id)
    assert row.request_id is not None, "Phase 8's cost query counts titles by request_id"


def test_groq_names_the_chat_when_gemini_fails(user, providers):
    providers["gemini"].fail(TRANSIENT["503"]())
    providers["groq"].answer("Pricing ", "in August")

    assert title_for(QUESTION, user.id) == "Pricing in August"

    [request] = providers["groq"].requests
    assert "tools" not in request and "tool_choice" not in request, "a plain completion must send no tool list"
    gemini, groq = usage_rows()
    assert (gemini.provider, gemini.outcome, gemini.input_tokens) == ("gemini", "failed", None)
    assert (groq.provider, groq.outcome, groq.input_tokens, groq.output_tokens) == ("groq", "fallback", 12, 7)
    assert gemini.request_id == groq.request_id


def test_an_unusable_gemini_reply_also_falls_back(user, providers):
    providers["gemini"].reply("  ")
    providers["groq"].answer("Pricing in August")

    assert title_for(QUESTION, user.id) == "Pricing in August"
    assert [r.outcome for r in usage_rows()] == ["failed", "fallback"]


def test_the_question_is_the_title_when_both_providers_fail(user, providers):
    providers["gemini"].fail(RuntimeError("boom"))
    providers["groq"].status(400)

    assert title_for(QUESTION, user.id) == titles.fallback_title(QUESTION)
    assert [(r.provider, r.outcome) for r in usage_rows()] == [("gemini", "failed"), ("groq", "failed")]


def test_without_a_groq_key_gemini_failing_goes_straight_to_the_question(user, providers, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    providers["gemini"].fail(RuntimeError("boom"))

    assert title_for("Budget?", user.id) == "Budget?"
    assert providers["groq"].calls == 0
