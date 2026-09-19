"""
Ask AI Phase 2 - meetings.summary_embedding.

Two writers, one rule: a meeting's summary vector must come from the same
provider as its chunks (meetings.embedding_provider describes both), because
Gemini and Jina vectors live in different spaces and a mismatch doesn't error,
it just silently ranks the meeting wrong.

  - index_transcript embeds it in the same call as the chunks and commits it
    with them.
  - app.ask_ai.backfill fills it in for meetings indexed before that, with
    each meeting's own provider and no fallback.

Gemini is faked at client.models.embed_content and Jina at httpx.post (see
tests/fake_ai_providers.py); the retry loops and the Jina module are real.
Postgres only.
"""
import uuid

import pytest

from app.ask_ai import backfill
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import AiUsageEvent, Meeting, MeetingChunk
from app.services import embedding_service

from tests.fake_ai_providers import NON_TRANSIENT, TRANSIENT, FakeGeminiEmbed, FakeJina, install_fast_sleeps

EMBED_DIM = 768
GEMINI_VECTOR = [0.25] * EMBED_DIM   # what FakeGeminiEmbed returns
JINA_VECTOR = [0.75] * EMBED_DIM     # what FakeJina returns

TRANSCRIPT = {
    "title": "Launch review",
    "summary": "The launch date was confirmed.",
    "key_points": ["QA signed off", "Launch stays on the 14th"],
    "action_items": [], "conclusion": "Launch on the 14th.",
    "conversation": [
        {"text": "Priya - Are we still on for the 14th?", "timestamp_start": "00:00", "timestamp_end": "00:03"},
        {"text": "Rohan - Yes, QA signed off this morning.", "timestamp_start": "00:04", "timestamp_end": "00:07"},
    ],
}
NO_SUMMARY = {**TRANSCRIPT, "title": "", "summary": "", "key_points": []}


@pytest.fixture
def providers(monkeypatch):
    log = []
    monkeypatch.setattr(settings, "jina_api_key", "stub-jina-key")
    monkeypatch.setattr(settings, "embedding_dimensions", EMBED_DIM)
    return {
        "log": log,
        "gemini": FakeGeminiEmbed(log, EMBED_DIM).install(monkeypatch),
        "jina": FakeJina(log, EMBED_DIM).install(monkeypatch),
        "slept": install_fast_sleeps(monkeypatch),
    }


def make_meeting(db, user_id, *, transcript=TRANSCRIPT, status="completed", embedding_provider=None,
                 summary_embedding=None):
    meeting = Meeting(
        id=uuid.uuid4(), user_id=user_id, meeting_url="https://zoom.us/j/1", platform="zoom",
        status=status, embedding_provider=embedding_provider, summary_embedding=summary_embedding,
    )
    # Left unset rather than set to None when absent: an explicit None in a
    # JSONB column is stored as JSON null, not SQL NULL, and a real meeting
    # with no transcript has never had the column written at all.
    if transcript is not None:
        meeting.transcript = transcript
    db.add(meeting)
    db.commit()
    return meeting.id


def state(meeting_id):
    """(embedding_provider, summary_embedding as a list or None, chunk count) on a fresh connection."""
    other = SessionLocal()
    try:
        provider, vector = other.query(Meeting.embedding_provider, Meeting.summary_embedding).filter(Meeting.id == meeting_id).one()
        chunks = other.query(MeetingChunk).filter(MeetingChunk.meeting_id == meeting_id).count()
        return provider, (None if vector is None else [float(v) for v in vector]), chunks
    finally:
        other.close()


def embedding_rows():
    other = SessionLocal()
    try:
        return other.query(AiUsageEvent).filter(AiUsageEvent.operation == "embedding").order_by(AiUsageEvent.id).all()
    finally:
        other.close()


# ---------------------------------------------------------------------------
# summary_document
# ---------------------------------------------------------------------------

def test_the_summary_document_is_the_title_summary_and_key_points():
    assert embedding_service.summary_document(TRANSCRIPT) == (
        "Launch review\n\n"
        "The launch date was confirmed.\n\n"
        "- QA signed off\n- Launch stays on the 14th"
    )


def test_the_summary_document_skips_missing_parts_and_blank_key_points():
    transcript = {"title": "  ", "summary": "Budget talk.", "key_points": ["", "  ", None, "Cut Q3 spend"]}

    assert embedding_service.summary_document(transcript) == "Budget talk.\n\n- Cut Q3 spend"
    assert embedding_service.summary_document(NO_SUMMARY) == ""
    assert embedding_service.summary_document({}) == ""


# ---------------------------------------------------------------------------
# New meetings: index_transcript
# ---------------------------------------------------------------------------

def test_indexing_embeds_the_summary_in_the_same_call_as_the_chunks(db, user, providers):
    meeting_id = make_meeting(db, user.id)

    embedding_service.index_transcript(db, str(meeting_id), TRANSCRIPT)

    [contents] = providers["gemini"].inputs
    assert contents[-1] == embedding_service.summary_document(TRANSCRIPT), "the summary must be the last document of the one batch"
    provider, vector, chunks = state(meeting_id)
    assert (provider, vector) == ("gemini", GEMINI_VECTOR)
    assert chunks == len(contents) - 1, "the summary document was stored as a chunk"


@pytest.mark.parametrize("failure", ["503", "connect-error"])
def test_a_jina_fallback_embeds_the_summary_with_jina_too(db, user, providers, failure):
    meeting_id = make_meeting(db, user.id)
    providers["gemini"].fail(TRANSIENT[failure], times=4)

    embedding_service.index_transcript(db, str(meeting_id), TRANSCRIPT)

    [request] = providers["jina"].requests
    assert request["json"]["input"][-1] == embedding_service.summary_document(TRANSCRIPT)
    assert request["json"]["task"] == "retrieval.passage"
    provider, vector, _ = state(meeting_id)
    assert (provider, vector) == ("jina", JINA_VECTOR), "summary and chunks came from different providers"


def test_a_transcript_with_nothing_to_summarise_leaves_the_vector_null(db, user, providers):
    meeting_id = make_meeting(db, user.id, transcript=NO_SUMMARY)

    embedding_service.index_transcript(db, str(meeting_id), NO_SUMMARY)

    [contents] = providers["gemini"].inputs
    provider, vector, chunks = state(meeting_id)
    assert (provider, vector) == ("gemini", None)
    assert chunks == len(contents), "every embedded document should be a chunk"


def test_reindexing_with_no_summary_clears_a_vector_from_the_old_provider(db, user, providers):
    """A stale Jina summary vector next to fresh Gemini chunks would be compared against Gemini queries."""
    meeting_id = make_meeting(db, user.id, transcript=NO_SUMMARY, embedding_provider="jina", summary_embedding=JINA_VECTOR)

    embedding_service.index_transcript(db, str(meeting_id), NO_SUMMARY)

    assert state(meeting_id)[:2] == ("gemini", None)


@pytest.mark.parametrize("failure", list(NON_TRANSIENT))
def test_a_failed_embed_leaves_the_existing_summary_vector_alone(db, user, providers, failure):
    meeting_id = make_meeting(db, user.id, embedding_provider="jina", summary_embedding=JINA_VECTOR)
    providers["gemini"].fail(NON_TRANSIENT[failure])

    with pytest.raises(Exception):
        embedding_service.index_transcript(db, str(meeting_id), TRANSCRIPT)
    db.rollback()

    assert state(meeting_id)[:2] == ("jina", JINA_VECTOR)


def test_the_usage_row_pays_for_the_summary_document_too(db, user, providers):
    meeting_id = make_meeting(db, user.id)

    embedding_service.index_transcript(db, str(meeting_id), TRANSCRIPT)

    [contents] = providers["gemini"].inputs
    [row] = embedding_rows()
    assert row.input_tokens == embedding_service.estimate_tokens(sum(len(t) for t in contents))


# ---------------------------------------------------------------------------
# Existing meetings: app.ask_ai.backfill
# ---------------------------------------------------------------------------

def test_the_backfill_embeds_each_meeting_with_its_own_provider(db, user, providers):
    gemini = make_meeting(db, user.id, embedding_provider="gemini")
    jina = make_meeting(db, user.id, embedding_provider="jina")
    unset = make_meeting(db, user.id, embedding_provider=None)

    stats = backfill.backfill()

    assert state(gemini)[1] == GEMINI_VECTOR
    assert state(jina)[1] == JINA_VECTOR
    assert state(unset)[1] == GEMINI_VECTOR, "a meeting with no provider recorded defaults to Gemini"
    assert providers["gemini"].calls == 1, "one Gemini call for the whole batch"
    [request] = providers["jina"].requests
    assert request["json"]["task"] == "retrieval.passage"
    assert request["json"]["input"] == [embedding_service.summary_document(TRANSCRIPT)]
    assert stats == {"embedded": 3, "skipped": 0, "changed": 0, "batches": 1}


def test_the_backfill_only_touches_completed_meetings_still_missing_a_vector(db, user, providers):
    done = make_meeting(db, user.id, embedding_provider="gemini", summary_embedding=[0.5] * EMBED_DIM)
    failed = make_meeting(db, user.id, status="failed")
    no_transcript = make_meeting(db, user.id, transcript=None)
    nothing_to_embed = make_meeting(db, user.id, transcript=NO_SUMMARY)

    stats = backfill.backfill()

    assert state(done)[1] == [0.5] * EMBED_DIM
    assert state(failed)[1] is None
    assert state(no_transcript)[1] is None
    assert state(nothing_to_embed)[1] is None
    assert providers["log"] == [], "nothing here needed an embedding call"
    assert stats == {"embedded": 0, "skipped": 1, "changed": 0, "batches": 1}


def test_the_backfill_is_idempotent(db, user, providers):
    meeting_id = make_meeting(db, user.id, embedding_provider="gemini")

    first = backfill.backfill()
    second = backfill.backfill()

    assert first["embedded"] == 1
    assert second == {"embedded": 0, "skipped": 0, "changed": 0, "batches": 0}
    assert providers["gemini"].calls == 1
    assert len(embedding_rows()) == 1
    assert state(meeting_id)[1] == GEMINI_VECTOR


def test_the_backfill_pages_past_meetings_it_cannot_embed(db, user, providers):
    """Those stay NULL for good; a first-page-only loop would spin on them forever."""
    for _ in range(3):
        make_meeting(db, user.id, transcript=NO_SUMMARY)
    wanted = [make_meeting(db, user.id, embedding_provider="gemini") for _ in range(3)]

    stats = backfill.backfill(batch_size=2)

    assert all(state(m)[1] == GEMINI_VECTOR for m in wanted)
    assert stats["embedded"] == 3 and stats["skipped"] == 3
    assert stats["batches"] == 3


def test_the_backfill_records_one_embedding_usage_row_per_meeting(db, user, providers):
    gemini = make_meeting(db, user.id, embedding_provider="gemini")
    jina = make_meeting(db, user.id, embedding_provider="jina")

    backfill.backfill()

    rows = {row.meeting_id: row for row in embedding_rows()}
    assert set(rows) == {gemini, jina}
    tokens = embedding_service.estimate_tokens(len(embedding_service.summary_document(TRANSCRIPT)))
    assert (rows[gemini].provider, rows[gemini].outcome, rows[gemini].input_tokens) == ("gemini", "ok", tokens)
    assert (rows[jina].provider, rows[jina].outcome, rows[jina].input_tokens) == ("jina", "fallback", tokens)
    assert all(r.user_id == user.id and r.estimated for r in rows.values())


def test_the_backfill_never_falls_back_to_the_other_provider(db, user, providers):
    """A Jina summary vector for a Gemini meeting would match nothing, silently."""
    meeting_id = make_meeting(db, user.id, embedding_provider="gemini")
    providers["gemini"].fail(TRANSIENT["503"], times=4)

    with pytest.raises(Exception):
        backfill.backfill()

    assert providers["jina"].calls == 0
    assert state(meeting_id)[1] is None
    assert embedding_rows() == []


def test_the_backfill_skips_a_meeting_reindexed_while_it_ran(db, user, providers, monkeypatch):
    meeting_id = make_meeting(db, user.id, embedding_provider="gemini")
    real_embed = embedding_service.embed_documents_with_provider

    def reindexed_by_jina_meanwhile(texts, provider):
        vectors = real_embed(texts, provider)
        other = SessionLocal()
        other.query(Meeting).filter(Meeting.id == meeting_id).update(
            {Meeting.embedding_provider: "jina", Meeting.summary_embedding: JINA_VECTOR}
        )
        other.commit()
        other.close()
        return vectors

    monkeypatch.setattr(embedding_service, "embed_documents_with_provider", reindexed_by_jina_meanwhile)

    stats = backfill.backfill()

    assert state(meeting_id)[:2] == ("jina", JINA_VECTOR), "the backfill overwrote a fresher vector"
    assert stats == {"embedded": 0, "skipped": 0, "changed": 1, "batches": 1}
    assert embedding_rows() == []


def test_a_dry_run_counts_without_calling_or_writing(db, user, providers):
    meeting_id = make_meeting(db, user.id, embedding_provider="gemini")
    make_meeting(db, user.id, transcript=NO_SUMMARY)

    stats = backfill.backfill(dry_run=True)

    assert stats == {"embedded": 1, "skipped": 1, "changed": 0, "batches": 1}
    assert providers["log"] == []
    assert state(meeting_id)[1] is None
