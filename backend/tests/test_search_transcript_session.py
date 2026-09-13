"""
search_transcript must not hold a pooled connection while it embeds the query.

embed_query can take ~10s on a Gemini 429 (four attempts with backoff sleeps, then
Jina), and chat runs tool calls in parallel, so one turn could hold several of the
backend's 8 connections. The stub reads engine.pool.checkedout() from inside
embed_query. The vector search itself runs against real pgvector rows, so moving
the session boundaries is also checked for not changing results.

Postgres (pgvector) is required.
"""
import types
import uuid

import pytest

from app.api import auth
from app.db.database import SessionLocal, engine
from app.db.models import Meeting, MeetingChunk, User
from app.rag import tools

DIMS = 768


def vec(*leading):
    """A 768-d vector with the given leading components - deterministic distances."""
    return list(leading) + [0.0] * (DIMS - len(leading))


def add_user(user_id=None, email=None):
    user_id = user_id or uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(User(id=user_id, email=email or f"u-{user_id}@example.com"))
        db.commit()
    finally:
        db.close()
    return str(user_id)


def add_meeting(user_id, status="completed", embedding_provider=None, chunks=()):
    meeting_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(Meeting(id=meeting_id, user_id=user_id, meeting_url="https://zoom.us/j/1", platform="zoom",
                       status=status, title="Planning", embedding_provider=embedding_provider))
        db.flush()
        for i, (content, embedding) in enumerate(chunks):
            db.add(MeetingChunk(meeting_id=meeting_id, chunk_index=i, content=content, speakers=["Asha"],
                                timestamp_start=f"00:0{i}", timestamp_end=f"00:0{i + 1}", embedding=embedding))
        db.commit()
    finally:
        db.close()
    return str(meeting_id)


@pytest.fixture(autouse=True)
def nothing_checked_out_before():
    auth.user_row_cache.clear()
    assert engine.pool.checkedout() == 0, "a previous test leaked a connection"
    yield
    auth.user_row_cache.clear()


def ctx(meeting_id, user_id):
    return types.SimpleNamespace(state={"meeting_id": meeting_id, "user_id": user_id})


# ---------------------------------------------------------------------------
# 1. search_transcript
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_embed(monkeypatch):
    calls = []

    def embed(query, provider="gemini", **attribution):  # meeting_id/user_id: B4 usage attribution
        calls.append({"query": query, "provider": provider, "checked_out": engine.pool.checkedout()})
        return vec(1.0)

    monkeypatch.setattr(tools, "embed_query", embed)
    return calls


def test_search_transcript_holds_no_connection_while_embedding(fake_embed):
    user_id = add_user()
    meeting_id = add_meeting(user_id, chunks=[("hello", vec(1.0))])

    tools.search_transcript("budget", ctx(meeting_id, user_id))

    assert [c["checked_out"] for c in fake_embed] == [0], f"checked out during embed_query: {fake_embed}"
    assert engine.pool.checkedout() == 0


def test_search_transcript_returns_the_same_chunks_in_the_same_order(fake_embed):
    """Moving session boundaries must not change what the vector search returns."""
    user_id = add_user()
    # Query embeds to vec(1.0). Cosine distance: exact 0.0, near 0.2, far 1.0.
    meeting_id = add_meeting(user_id, chunks=[
        ("far - orthogonal", vec(0.0, 1.0)),
        ("exact match", vec(1.0, 0.0)),
        ("near match", vec(0.8, 0.6)),
    ])
    # Another meeting's identical vector must never leak into this meeting's results.
    add_meeting(user_id, chunks=[("other meeting", vec(1.0))])

    result = tools.search_transcript("budget", ctx(meeting_id, user_id))

    assert [m["content"] for m in result["matches"]] == ["exact match", "near match", "far - orthogonal"]
    assert result["matches"][0] == {
        "content": "exact match", "speakers": ["Asha"], "timestamp_start": "00:01", "timestamp_end": "00:02",
    }
    assert fake_embed[0]["query"] == "budget"


@pytest.mark.parametrize("stored, expected", [(None, "gemini"), ("gemini", "gemini"), ("jina", "jina")])
def test_search_transcript_embeds_with_the_meetings_own_provider(fake_embed, stored, expected):
    user_id = add_user()
    meeting_id = add_meeting(user_id, embedding_provider=stored, chunks=[("x", vec(1.0))])

    tools.search_transcript("budget", ctx(meeting_id, user_id))

    assert fake_embed[0]["provider"] == expected


def test_search_transcript_refuses_another_users_meeting_without_embedding(fake_embed):
    owner = add_user()
    meeting_id = add_meeting(owner, chunks=[("secret", vec(1.0))])

    result = tools.search_transcript("budget", ctx(meeting_id, add_user()))

    assert result == {"note": "Meeting not found or unauthorized."}
    assert fake_embed == []
    assert engine.pool.checkedout() == 0


def test_search_transcript_with_no_chunks_keeps_its_note(fake_embed):
    user_id = add_user()
    meeting_id = add_meeting(user_id)

    result = tools.search_transcript("budget", ctx(meeting_id, user_id))

    assert result == {"matches": [], "note": "No indexed transcript chunks found for this meeting."}
    assert engine.pool.checkedout() == 0


