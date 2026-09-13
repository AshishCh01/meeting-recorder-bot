"""
Phase B4 - AI spend recorded as data.

cost_tracker.record_usage writes one ai_usage_events row per AI call group and
one "[cost]" log line. This file proves four things:

  1. Every paid path writes the right row - provider, model, units, user,
     meeting, outcome, estimated flag - including all three fallbacks and the
     chat-query embedding that used to go unrecorded.
  2. Recording never breaks the work it records: with the table genuinely
     unusable (renamed away mid-test, not mocked), chat still answers and a
     meeting still transcribes and indexes.
  3. Recording never takes a pooled connection it should not: none during a
     chat model call, and none of its own inside transcription or indexing,
     which run in worker jobs that already hold one against a pool with no
     overflow.
  4. The saved queries in backend/sql/ai_usage_queries.sql run and return the
     right numbers against seeded rows - that file is what the plan tells you
     to paste into the Supabase SQL editor.

Providers are faked at their API calls (tests/fake_ai_providers.py and the
transcription fakes in test_transcription_queue.py). Postgres only.
"""
import asyncio
import contextlib
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import settings
from app.db import database
from app.db.database import SessionLocal, engine
from app.db.models import AiUsageEvent, Meeting
from app.rag import tools
from app.services import cost_tracker, embedding_service, transcription_service

from tests.fake_ai_providers import TRANSIENT, FakeGeminiEmbed, FakeJina, call, install_fast_sleeps
from tests.test_chat_fallback_ladder import ask, chat, stored  # noqa: F401 - chat is a fixture
from tests.test_transcription_queue import (  # noqa: F401 - fixtures
    TRANSCRIPT,
    full_pipeline,
    gemini_down_sarvam_up,
    stub_embeddings,
)

QUERIES_FILE = Path(__file__).resolve().parent.parent / "sql" / "ai_usage_queries.sql"


@pytest.fixture(autouse=True)
def rates(monkeypatch):
    """Every rate pinned to a distinct non-zero value, so a wrong rate shows."""
    monkeypatch.setattr(settings, "gemini_input_cost_per_mtok", 0.30)
    monkeypatch.setattr(settings, "gemini_output_cost_per_mtok", 2.50)
    monkeypatch.setattr(settings, "gemini_embedding_cost_per_mtok", 0.15)
    monkeypatch.setattr(settings, "groq_input_cost_per_mtok", 0.10)
    monkeypatch.setattr(settings, "groq_output_cost_per_mtok", 0.40)
    monkeypatch.setattr(settings, "jina_embedding_cost_per_mtok", 0.02)
    monkeypatch.setattr(settings, "sarvam_cost_per_audio_hour", 1.80)


def usage_rows():
    other = SessionLocal()
    try:
        return other.query(AiUsageEvent).order_by(AiUsageEvent.id).all()
    finally:
        other.close()


@contextlib.contextmanager
def usage_table_unavailable():
    """
    Makes every insert into ai_usage_events genuinely fail - the state of a
    deploy whose migration has not run - and puts the table back afterwards.
    """
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE ai_usage_events RENAME TO ai_usage_events_hidden"))
    try:
        yield
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE ai_usage_events_hidden RENAME TO ai_usage_events"))


def usd(value):
    return Decimal(str(round(value, 6)))


# ---------------------------------------------------------------------------
# 1. record_usage itself
# ---------------------------------------------------------------------------

def test_record_usage_stores_a_row_and_logs_one_cost_line(caplog):
    meeting_id, user_id, request_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    with caplog.at_level(logging.INFO, logger="app.services.cost_tracker"):
        cost_tracker.record_usage(
            operation="chat", provider="gemini", model="gemini-x", outcome="ok",
            request_id=request_id, user_id=user_id, meeting_id=meeting_id,
            input_tokens=1200, output_tokens=300, usd=0.00111, estimated=False,
        )

    [row] = usage_rows()
    assert (row.operation, row.provider, row.model, row.outcome) == ("chat", "gemini", "gemini-x", "ok")
    assert (row.request_id, row.user_id, row.meeting_id) == (request_id, user_id, meeting_id)
    assert (row.input_tokens, row.output_tokens, row.audio_seconds) == (1200, 300, None)
    assert row.usd == usd(0.00111)
    assert row.estimated is False
    assert row.created_at is not None

    [line] = [r.getMessage() for r in caplog.records if r.name == "app.services.cost_tracker"]
    assert line.startswith("[cost] chat provider=gemini model=gemini-x outcome=ok ")
    assert "in_tok=1200 out_tok=300 usd=0.001110" in line
    assert "audio_sec" not in line and "estimated" not in line, "unset fields must be left off, not printed as None"


def test_record_usage_never_raises_when_the_table_is_unusable(caplog):
    with usage_table_unavailable():
        with caplog.at_level(logging.WARNING, logger="app.services.cost_tracker"):
            cost_tracker.record_usage(operation="chat", provider="gemini", outcome="ok", usd=0.1)

    assert usage_rows() == []
    assert any("could not store chat usage" in r.getMessage() for r in caplog.records)


def test_a_staged_row_persists_only_with_the_callers_commit(db, user):
    meeting = Meeting(id=uuid.uuid4(), user_id=user.id, meeting_url="https://zoom.us/j/1", platform="zoom", status="transcribing")
    db.add(meeting)
    db.commit()

    cost_tracker.record_usage(operation="transcription", provider="gemini", outcome="ok", meeting_id=meeting.id, db=db)
    assert usage_rows() == [], "a staged row was visible before the caller committed"
    db.rollback()
    assert usage_rows() == [], "a rolled-back caller still recorded usage"

    cost_tracker.record_usage(operation="transcription", provider="gemini", outcome="ok", meeting_id=meeting.id, db=db)
    db.commit()
    assert [r.meeting_id for r in usage_rows()] == [meeting.id]


def test_a_failed_staged_insert_does_not_poison_the_callers_transaction(db, user):
    """The savepoint is the point: the caller's own work still commits."""
    meeting = Meeting(id=uuid.uuid4(), user_id=user.id, meeting_url="https://zoom.us/j/1", platform="zoom", status="transcribing")
    db.add(meeting)
    db.commit()

    with usage_table_unavailable():
        db.query(Meeting).filter(Meeting.id == meeting.id).update({"status": "completed"})
        cost_tracker.record_usage(operation="transcription", provider="gemini", outcome="ok", meeting_id=meeting.id, db=db)
        db.commit()

    other = SessionLocal()
    try:
        assert other.query(Meeting.status).filter(Meeting.id == meeting.id).scalar() == "completed"
    finally:
        other.close()
    assert usage_rows() == []


# ---------------------------------------------------------------------------
# 2. Chat
# ---------------------------------------------------------------------------

def test_a_gemini_chat_turn_records_one_row_with_every_iterations_tokens(chat):
    chat.gemini.reply("Let me check. ", call("_get_action_items"), usage=(500, 40))
    chat.gemini.reply("Rohan owns the API work.", usage=(900, 60))

    ask(chat, "Who owns the API work?")

    [row] = usage_rows()
    assert (row.operation, row.provider, row.outcome) == ("chat", "gemini", "ok")
    assert row.model == settings.rag_agent_model
    assert (str(row.user_id), str(row.meeting_id)) == (chat.user_id, chat.meeting_id)
    assert (row.input_tokens, row.output_tokens) == (1400, 100)
    assert row.usd == usd(1400 / 1e6 * 0.30 + 100 / 1e6 * 2.50)
    assert row.request_id is not None
    assert row.estimated is False


def test_a_groq_fallback_turn_records_both_providers_under_one_request(chat):
    chat.gemini.fail(TRANSIENT["503"], times=2)
    chat.groq.answer("The launch is on the 14th.")   # the fake reports 12 in / 7 out

    ask(chat, "When is the launch?")

    gemini, groq = usage_rows()
    assert (gemini.provider, gemini.outcome) == ("gemini", "failed")
    assert (gemini.input_tokens, gemini.output_tokens) == (None, None), "no usage was reported, so none may be claimed"
    assert gemini.usd == 0
    assert (groq.provider, groq.outcome, groq.model) == ("groq", "fallback", settings.groq_chat_model)
    assert (groq.input_tokens, groq.output_tokens) == (12, 7)
    assert groq.usd == usd(12 / 1e6 * 0.10 + 7 / 1e6 * 0.40), "Groq is no longer a hard-coded 0"
    assert gemini.request_id == groq.request_id
    assert {str(r.user_id) for r in (gemini, groq)} == {chat.user_id}


def test_a_turn_where_gemini_and_groq_both_fail_records_both_as_failed(chat):
    chat.gemini.fail(TRANSIENT["429"], times=2)
    chat.groq.status(503, times=3)

    ask(chat, "When is the launch?")

    assert [(r.provider, r.outcome, r.input_tokens) for r in usage_rows()] == [
        ("gemini", "failed", None),
        ("groq", "failed", None),
    ]
    assert stored(chat) == [], "a failed turn still stores no chat message"


def test_a_failure_after_a_paid_tool_turn_still_records_the_tokens_spent(chat, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    chat.gemini.reply(call("_get_action_items"), usage=(300, 20))
    chat.gemini.fail(TRANSIENT["503"], times=2)

    ask(chat, "Who owns the API work?")

    [row] = usage_rows()
    assert (row.provider, row.outcome, row.input_tokens, row.output_tokens) == ("gemini", "failed", 300, 20)


def test_an_unexpected_chat_error_still_records_usage_before_propagating(chat):
    chat.gemini.reply(call("_get_action_items"), usage=(300, 20))
    chat.gemini.reply("The launch ", ValueError("unexpected response shape"))

    from app.rag import chat_service

    async def drain():
        with pytest.raises(ValueError):
            async for _ in chat_service.ask_question_stream(chat.meeting_id, "Who?", None, chat.user_id):
                pass

    asyncio.run(drain())

    [row] = usage_rows()
    assert (row.outcome, row.input_tokens) == ("failed", 300)


def test_each_chat_turn_gets_its_own_request_id(chat):
    chat.gemini.reply("First.", usage=(10, 1)).reply("Second.", usage=(20, 2))

    ask(chat, "One?", "Two?")

    first, second = usage_rows()
    assert first.request_id != second.request_id


def test_chat_still_answers_and_stores_when_usage_cannot_be_recorded(chat):
    chat.gemini.reply("The launch is on the 14th.", usage=(100, 10))

    with usage_table_unavailable():
        [events] = ask(chat, "When is the launch?")

    assert [e["type"] for e in events] == ["delta", "done"]
    assert stored(chat)[1][1] == "The launch is on the 14th."
    assert usage_rows() == []


def test_no_pooled_connection_is_held_while_the_chat_model_answers(chat, db):
    """Recording happens after the model call, in a thread, on a short session."""
    # The fixtures' own session refreshed a row and still holds a connection;
    # release it, or the measurement below counts the test, not the app.
    db.close()
    assert engine.pool.checkedout() == 0, "a fixture leaked a connection - the measurement would be off"
    seen = []
    chat.gemini.on_call = lambda: seen.append(engine.pool.checkedout())
    chat.gemini.reply(call("_get_action_items"), usage=(50, 5))
    chat.gemini.reply("Rohan owns the API work.", usage=(80, 8))

    ask(chat, "Who owns the API work?")

    assert seen == [0, 0], f"connections checked out during the model call: {seen}"
    assert engine.pool.checkedout() == 0
    assert len(usage_rows()) == 1


# ---------------------------------------------------------------------------
# 3. Transcription and indexing
# ---------------------------------------------------------------------------

def make_transcribing_meeting(db, user):
    meeting = Meeting(id=uuid.uuid4(), user_id=user.id, meeting_url="https://meet.google.com/abc-defg-hij", platform="google", status="transcribing")
    db.add(meeting)
    db.commit()
    return meeting


@pytest.fixture
def no_second_connection(monkeypatch):
    """
    Tripwire on the only way record_usage opens a session of its own. The
    transcription and indexing paths must stage into the session they already
    hold instead.
    """
    opened = []
    real = database.SessionLocal

    def tripwire():
        opened.append("SessionLocal")
        return real()

    monkeypatch.setattr(database, "SessionLocal", tripwire)
    return opened


def test_a_gemini_transcription_records_transcription_and_embedding_rows(db, user, full_pipeline, stub_embeddings, no_second_connection):
    meeting = make_transcribing_meeting(db, user)

    transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    transcription, embedding = usage_rows()
    assert (transcription.operation, transcription.provider, transcription.outcome) == ("transcription", "gemini", "ok")
    assert transcription.model == settings.gemini_model
    assert (transcription.input_tokens, transcription.output_tokens) == (100, 50)   # full_pipeline's usage
    assert transcription.audio_seconds == 42.0
    assert transcription.usd == usd(100 / 1e6 * 0.30 + 50 / 1e6 * 2.50)
    assert (transcription.user_id, transcription.meeting_id) == (user.id, meeting.id)

    assert (embedding.operation, embedding.provider, embedding.outcome) == ("embedding", "gemini", "ok")
    assert embedding.estimated is True
    expected_tokens = cost_tracker.estimate_tokens(
        sum(len(c["content"]) for c in embedding_service._build_chunks(TRANSCRIPT["conversation"]))
    )
    assert embedding.input_tokens == expected_tokens
    assert embedding.usd == usd(expected_tokens / 1e6 * 0.15)
    assert (embedding.user_id, embedding.meeting_id) == (user.id, meeting.id)

    assert no_second_connection == [], "transcription opened a second connection just to record usage"


def test_a_sarvam_fallback_records_audio_seconds_and_no_gemini_transcription_row(db, user, gemini_down_sarvam_up, stub_embeddings, no_second_connection):
    meeting = make_transcribing_meeting(db, user)

    transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    transcription, embedding = usage_rows()
    assert (transcription.provider, transcription.outcome, transcription.model) == ("sarvam", "fallback", settings.sarvam_stt_model)
    assert transcription.audio_seconds == 42.0
    assert (transcription.input_tokens, transcription.output_tokens) == (None, None)
    assert transcription.usd == usd(42.0 / 3600 * 1.80)
    assert transcription.user_id == user.id
    assert embedding.operation == "embedding"
    assert no_second_connection == []


def test_a_meeting_still_transcribes_and_indexes_when_usage_cannot_be_recorded(db, user, full_pipeline, stub_embeddings):
    meeting = make_transcribing_meeting(db, user)

    with usage_table_unavailable():
        result = transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    assert result == TRANSCRIPT
    other = SessionLocal()
    try:
        row = other.query(Meeting).filter(Meeting.id == meeting.id).one()
        assert (row.status, row.embedding_provider) == ("completed", "gemini")
    finally:
        other.close()
    assert usage_rows() == []


def test_the_transcription_row_survives_an_indexing_failure(db, user, full_pipeline, monkeypatch):
    """
    The transcript is committed before indexing starts, and its usage row with
    it. The failed index attempt's own row rolls back with the index - a known
    limit, documented in the plan.
    """
    meeting = make_transcribing_meeting(db, user)

    def embed_down(texts):
        raise RuntimeError("embedding provider down")

    monkeypatch.setattr(embedding_service, "_embed_documents", embed_down)

    with pytest.raises(transcription_service.IndexingFailed):
        transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    assert [(r.operation, r.provider) for r in usage_rows()] == [("transcription", "gemini")]


def test_a_jina_indexing_fallback_is_recorded_as_jina(db, user, monkeypatch):
    log = []
    monkeypatch.setattr(settings, "jina_api_key", "stub-jina-key")
    FakeGeminiEmbed(log).fail(TRANSIENT["503"], times=4).install(monkeypatch)
    FakeJina(log).install(monkeypatch)
    install_fast_sleeps(monkeypatch)
    meeting = Meeting(id=uuid.uuid4(), user_id=user.id, meeting_url="https://zoom.us/j/1", platform="zoom", status="completed", transcript=TRANSCRIPT)
    db.add(meeting)
    db.commit()

    embedding_service.index_transcript(db, str(meeting.id), TRANSCRIPT)

    [row] = usage_rows()
    assert (row.operation, row.provider, row.outcome, row.model) == ("embedding", "jina", "fallback", settings.jina_embedding_model)
    assert row.usd == usd(row.input_tokens / 1e6 * 0.02)
    assert row.user_id == user.id


# ---------------------------------------------------------------------------
# 4. Chat-query embeddings - unrecorded before B4
# ---------------------------------------------------------------------------

@pytest.fixture
def indexed_meeting(db, user):
    def make(provider):
        meeting = Meeting(id=uuid.uuid4(), user_id=user.id, meeting_url="https://zoom.us/j/1", platform="zoom", status="completed", transcript=TRANSCRIPT, embedding_provider=provider)
        db.add(meeting)
        db.commit()
        return meeting
    return make


class _Ctx:
    def __init__(self, meeting, user):
        self.state = {"meeting_id": str(meeting.id), "user_id": str(user.id)}


@pytest.mark.parametrize("provider", ["gemini", "jina"])
def test_a_chat_search_records_a_query_embedding_row_attributed_to_the_meeting(user, indexed_meeting, monkeypatch, provider):
    log = []
    monkeypatch.setattr(settings, "jina_api_key", "stub-jina-key")
    FakeGeminiEmbed(log).install(monkeypatch)
    FakeJina(log).install(monkeypatch)
    meeting = indexed_meeting(provider)

    tools.search_transcript("when is the launch?", _Ctx(meeting, user))

    [row] = usage_rows()
    assert (row.operation, row.provider, row.outcome) == ("query_embedding", provider, "ok")
    assert (row.user_id, row.meeting_id) == (user.id, meeting.id)
    assert row.input_tokens == cost_tracker.estimate_tokens(len("when is the launch?"))
    assert row.estimated is True
    rate = 0.02 if provider == "jina" else 0.15
    assert row.usd == usd(row.input_tokens / 1e6 * rate)


def test_a_failed_query_embedding_records_nothing(user, indexed_meeting, monkeypatch):
    monkeypatch.setattr(settings, "jina_api_key", "")
    FakeGeminiEmbed([]).fail(TRANSIENT["503"], times=4).install(monkeypatch)
    install_fast_sleeps(monkeypatch)
    meeting = indexed_meeting("gemini")

    with pytest.raises(Exception):
        tools.search_transcript("when is the launch?", _Ctx(meeting, user))

    assert usage_rows() == []


# ---------------------------------------------------------------------------
# 5. The saved queries
# ---------------------------------------------------------------------------

def load_queries():
    sql = QUERIES_FILE.read_text(encoding="utf-8")
    parts = re.split(r"^-- name: (\w+)\s*$", sql, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def seed(rows):
    with engine.begin() as conn:
        for r in rows:
            conn.execute(text(
                "INSERT INTO ai_usage_events (created_at, request_id, operation, provider, user_id, meeting_id, usd, outcome) "
                "VALUES (:created_at, :request_id, :operation, :provider, :user_id, :meeting_id, :usd, :outcome)"
            ), r)


def test_the_queries_file_has_the_four_documented_queries():
    assert set(load_queries()) == {
        "spend_per_user_per_day", "chat_turn_cost_percentiles", "cost_per_meeting", "spend_and_fallbacks_by_provider",
    }


def test_the_saved_queries_return_the_right_numbers():
    now = datetime.now(timezone.utc)
    alice, bob = uuid.uuid4(), uuid.uuid4()
    m1, m2 = uuid.uuid4(), uuid.uuid4()
    turn_a, turn_b, turn_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    def row(operation, provider, usd_, outcome="ok", user=alice, meeting=m1, request=None, days_ago=0):
        return {
            "created_at": now - timedelta(days=days_ago), "request_id": request, "operation": operation,
            "provider": provider, "user_id": user, "meeting_id": meeting, "usd": usd_, "outcome": outcome,
        }

    seed([
        row("transcription", "gemini", 0.50),
        row("embedding", "gemini", 0.01),
        row("chat", "gemini", 0.02, request=turn_a),
        row("chat", "gemini", 0.00, outcome="failed", request=turn_b),
        row("chat", "groq", 0.04, outcome="fallback", request=turn_b),
        row("chat", "gemini", 0.10, user=bob, meeting=m2, request=turn_c),
        row("query_embedding", "jina", 0.001, user=bob, meeting=m2),
        row("transcription", "sarvam", 0.30, outcome="fallback", user=bob, meeting=m2, days_ago=40),  # outside both windows
    ])
    queries = load_queries()

    with engine.connect() as conn:
        per_user = conn.execute(text(queries["spend_per_user_per_day"])).mappings().all()
        turns = conn.execute(text(queries["chat_turn_cost_percentiles"])).mappings().all()
        per_meeting = {r["meeting_id"]: r for r in conn.execute(text(queries["cost_per_meeting"])).mappings().all()}
        by_provider = {(r["operation"], r["provider"]): r for r in conn.execute(text(queries["spend_and_fallbacks_by_provider"])).mappings().all()}

    # Last 30 days only: bob's 40-day-old Sarvam row is excluded.
    totals = {r["user_id"]: r["usd"] for r in per_user}
    assert totals == {alice: Decimal("0.570000"), bob: Decimal("0.101000")}

    # Three turns - 0.02, 0.04 (a failed Gemini row plus its Groq fallback), 0.10.
    [today] = turns
    assert today["turns"] == 3
    assert round(float(today["p50_usd"]), 6) == 0.04
    assert round(float(today["max_usd"]), 6) == 0.10

    # All time: m2 includes the 40-day-old Sarvam row.
    assert per_meeting[m1]["total_usd"] == Decimal("0.570000")
    assert per_meeting[m1]["transcription_usd"] == Decimal("0.500000")
    assert per_meeting[m1]["chat_usd"] == Decimal("0.060000")
    assert per_meeting[m2]["total_usd"] == Decimal("0.401000")
    assert per_meeting[m2]["query_embedding_usd"] == Decimal("0.001000")

    # Last 7 days.
    assert (by_provider[("chat", "gemini")]["ok"], by_provider[("chat", "gemini")]["failed"]) == (2, 1)
    assert by_provider[("chat", "groq")]["fallback"] == 1
    assert ("transcription", "sarvam") not in by_provider
