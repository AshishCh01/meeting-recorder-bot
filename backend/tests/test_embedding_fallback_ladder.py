"""
B3 item 3 - the embedding fallback ladder: Gemini -> Jina.

embedding_service retries Gemini's embed_content up to 4 times on a transient
failure, then hands the texts to Jina if a Jina key is configured. Transient
is gemini_errors.is_transient: 429, 503, 504 and httpx connection failures.
A 400-class error must not be retried or fall back - it would fail the same
way anywhere - and a missing Jina key must surface the Gemini error itself.

Two callers, both covered:

  - index_transcript -> _embed_documents: the transcript's chunks. Which
    provider embedded them is written to Meeting.embedding_provider, because
    Gemini and Jina vectors are not comparable.
  - embed_query: a chat question. A meeting indexed by Jina is queried with
    Jina directly. The query-side *fallback* for a Gemini-indexed meeting is
    deliberately not pinned here - see the finding in docs/scaling-plan.md B3.

Gemini is faked at client.models.embed_content and Jina at httpx.post (see
tests/fake_ai_providers.py); the retry loops, is_transient and the Jina
module are real. Postgres only.
"""
import uuid

import httpx
import pytest

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting, MeetingChunk
from app.services import embedding_service

from tests.fake_ai_providers import NON_TRANSIENT, TRANSIENT, FakeGeminiEmbed, FakeJina, install_fast_sleeps

EMBED_DIM = 768
TRANSCRIPT = {
    "title": "Launch review",
    "summary": "The launch date was confirmed.",
    "key_points": [], "action_items": [], "conclusion": "Launch on the 14th.",
    "conversation": [
        {"text": "Priya - Are we still on for the 14th?", "timestamp_start": "00:00", "timestamp_end": "00:03"},
        {"text": "Rohan - Yes, QA signed off this morning.", "timestamp_start": "00:04", "timestamp_end": "00:07"},
    ],
}


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


def make_meeting(db, user_id, *, embedding_provider=None):
    meeting = Meeting(
        id=uuid.uuid4(), user_id=user_id, meeting_url="https://zoom.us/j/1", platform="zoom",
        status="completed", transcript=TRANSCRIPT, embedding_provider=embedding_provider,
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


def seed_chunk(db, meeting_id, provider):
    db.add(MeetingChunk(
        meeting_id=meeting_id, chunk_index=0, content="chunk from the previous index", speakers=["Priya"],
        timestamp_start="00:00", timestamp_end="00:10", embedding=[0.1] * EMBED_DIM,
    ))
    db.query(Meeting).filter(Meeting.id == meeting_id).update({"embedding_provider": provider})
    db.commit()


def indexed_state(meeting_id):
    """(embedding_provider, [chunk contents]) read on a fresh connection."""
    other = SessionLocal()
    try:
        provider = other.query(Meeting.embedding_provider).filter(Meeting.id == meeting_id).scalar()
        contents = [c.content for c in other.query(MeetingChunk).filter(MeetingChunk.meeting_id == meeting_id).order_by(MeetingChunk.chunk_index)]
        return provider, contents
    finally:
        other.close()


# ---------------------------------------------------------------------------
# Indexing: index_transcript -> _embed_documents
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("failure", list(TRANSIENT))
def test_indexing_falls_back_to_jina_once_gemini_retries_are_exhausted(db, user, providers, failure):
    meeting = make_meeting(db, user.id)
    providers["gemini"].fail(TRANSIENT[failure], times=4)

    embedding_service.index_transcript(db, str(meeting.id), TRANSCRIPT)

    assert providers["log"] == ["gemini-embed:1", "gemini-embed:2", "gemini-embed:3", "gemini-embed:4", "jina:1"]
    assert len(providers["slept"]) == 3, "backoff between each of Gemini's 4 attempts, none after the last"
    jina = providers["jina"].requests[0]
    assert jina["json"]["task"] == "retrieval.passage", "chunks must be embedded as passages, not queries"
    assert jina["json"]["dimensions"] == EMBED_DIM
    assert jina["authorization"] == "Bearer stub-jina-key"
    provider, contents = indexed_state(meeting.id)
    assert provider == "jina", "chunks embedded by Jina were recorded as Gemini's"
    assert len(contents) >= 1


def test_a_gemini_failure_that_clears_within_its_retries_never_reaches_jina(db, user, providers):
    meeting = make_meeting(db, user.id)
    providers["gemini"].fail(TRANSIENT["504"], times=3)

    embedding_service.index_transcript(db, str(meeting.id), TRANSCRIPT)

    assert providers["log"] == ["gemini-embed:1", "gemini-embed:2", "gemini-embed:3", "gemini-embed:4"]
    assert providers["jina"].calls == 0
    assert indexed_state(meeting.id)[0] == "gemini"


@pytest.mark.parametrize("failure", list(NON_TRANSIENT))
def test_a_non_transient_gemini_error_is_not_retried_and_does_not_fall_back(db, user, providers, failure):
    """
    Fails cleanly: the Gemini error itself, on the first attempt, with the
    meeting's existing index untouched - the embed happens before the delete.
    """
    meeting = make_meeting(db, user.id)
    seed_chunk(db, meeting.id, provider="gemini")
    providers["gemini"].fail(NON_TRANSIENT[failure])

    with pytest.raises(Exception) as exc:
        embedding_service.index_transcript(db, str(meeting.id), TRANSCRIPT)
    db.rollback()

    assert getattr(exc.value, "code", None) == int(failure)
    assert providers["log"] == ["gemini-embed:1"]
    assert providers["slept"] == []
    assert indexed_state(meeting.id) == ("gemini", ["chunk from the previous index"])


@pytest.mark.parametrize("failure", ["429", "503", "504", "connect-error"])
def test_with_no_jina_key_indexing_raises_the_gemini_error_after_its_retries(db, user, providers, monkeypatch, failure):
    """
    Nothing to fall back to. The caller gets Gemini's own error - which
    transcribe_recording wraps as IndexingFailed, leaving a completed meeting
    for the queue to retry - and the existing index survives.
    """
    monkeypatch.setattr(settings, "jina_api_key", "")
    meeting = make_meeting(db, user.id)
    seed_chunk(db, meeting.id, provider="gemini")
    providers["gemini"].fail(TRANSIENT[failure], times=4)

    with pytest.raises((Exception,)) as exc:
        embedding_service.index_transcript(db, str(meeting.id), TRANSCRIPT)
    db.rollback()

    expected = TRANSIENT[failure]()
    assert type(exc.value) is type(expected)
    assert providers["gemini"].calls == 4
    assert providers["jina"].calls == 0
    assert indexed_state(meeting.id) == ("gemini", ["chunk from the previous index"])


def test_jina_failing_too_raises_and_leaves_the_existing_index(db, user, providers):
    meeting = make_meeting(db, user.id)
    seed_chunk(db, meeting.id, provider="gemini")
    providers["gemini"].fail(TRANSIENT["503"], times=4)
    providers["jina"].status(503, times=3)

    with pytest.raises(httpx.HTTPStatusError):
        embedding_service.index_transcript(db, str(meeting.id), TRANSCRIPT)
    db.rollback()

    assert providers["gemini"].calls == 4
    assert providers["jina"].calls == 3, "Jina's own retries"
    assert indexed_state(meeting.id) == ("gemini", ["chunk from the previous index"])


# ---------------------------------------------------------------------------
# Chat queries: embed_query
# ---------------------------------------------------------------------------

def test_a_jina_indexed_meeting_is_queried_with_jina_and_never_gemini(providers):
    vector = embedding_service.embed_query("when is the launch?", provider="jina")

    assert providers["log"] == ["jina:1"]
    request = providers["jina"].requests[0]["json"]
    assert request["task"] == "retrieval.query", "a query embedded as a passage matches worse"
    assert request["input"] == ["when is the launch?"]
    assert vector == [0.75] * EMBED_DIM


def test_a_gemini_query_that_clears_within_its_retries_stays_on_gemini(providers):
    providers["gemini"].fail(TRANSIENT["429"], times=2)

    vector = embedding_service.embed_query("when is the launch?", provider="gemini")

    assert providers["log"] == ["gemini-embed:1", "gemini-embed:2", "gemini-embed:3"]
    assert vector == [0.25] * EMBED_DIM


@pytest.mark.parametrize("failure", list(NON_TRANSIENT))
def test_a_non_transient_error_on_a_query_is_not_retried_and_does_not_fall_back(providers, failure):
    providers["gemini"].fail(NON_TRANSIENT[failure])

    with pytest.raises(Exception) as exc:
        embedding_service.embed_query("when is the launch?", provider="gemini")

    assert getattr(exc.value, "code", None) == int(failure)
    assert providers["log"] == ["gemini-embed:1"]


@pytest.mark.parametrize("failure", ["503", "504", "timeout"])
def test_with_no_jina_key_a_query_raises_the_gemini_error_after_its_retries(providers, monkeypatch, failure):
    monkeypatch.setattr(settings, "jina_api_key", "")
    providers["gemini"].fail(TRANSIENT[failure], times=4)

    with pytest.raises(Exception) as exc:
        embedding_service.embed_query("when is the launch?", provider="gemini")

    assert type(exc.value) is type(TRANSIENT[failure]())
    assert providers["gemini"].calls == 4
    assert providers["jina"].calls == 0
