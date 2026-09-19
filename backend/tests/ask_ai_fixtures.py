"""
Shared setup for the Ask AI tests: users and meetings written straight to the
database, with the dates, summaries and vectors a test needs.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.db.models import Meeting, MeetingChunk, User

EMBED_DIM = 768
KOLKATA = "Asia/Kolkata"


def add_user(db, email: str) -> uuid.UUID:
    user = User(email=email)
    db.add(user)
    db.commit()
    return user.id


def transcript(summary="A meeting.", key_points=(), action_items=(), conclusion="Done.", title="A meeting"):
    return {
        "title": title,
        "summary": summary,
        "key_points": list(key_points),
        "action_items": list(action_items),
        "conclusion": conclusion,
        "conversation": [],
    }


def meeting_row(user_id, *, when: datetime, title="A meeting", status="completed", scheduled=False,
                summary="A meeting.", key_points=(), action_items=(), platform="google_meet",
                duration_seconds=1800, embedding_provider=None, summary_embedding=None, meeting_id=None) -> dict:
    """
    One meetings row as a dict, for db.bulk_insert_mappings(Meeting, rows).
    `when` becomes scheduled_at if `scheduled`, else created_at - the two
    sources of a meeting's date.
    """
    row = {
        "id": meeting_id or uuid.uuid4(),
        "user_id": user_id,
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "platform": platform,
        "status": status,
        "title": title,
        "duration_seconds": duration_seconds,
        "transcript": transcript(summary, key_points, action_items, title=title),
        "embedding_provider": embedding_provider,
        "summary_embedding": summary_embedding,
    }
    if scheduled:
        row["scheduled_at"] = when
        row["created_at"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    else:
        row["created_at"] = when
    return row


def add_meetings(db, rows: list[dict]) -> list[uuid.UUID]:
    db.bulk_insert_mappings(Meeting, rows)
    db.commit()
    return [row["id"] for row in rows]


def add_meeting(db, user_id, **kwargs) -> uuid.UUID:
    return add_meetings(db, [meeting_row(user_id, **kwargs)])[0]


def add_chunk(db, meeting_id, content: str, embedding, *, chunk_index=0, speakers=("Priya",), timestamp_start="00:00"):
    db.add(MeetingChunk(
        meeting_id=meeting_id, chunk_index=chunk_index, content=content, speakers=list(speakers),
        timestamp_start=timestamp_start, timestamp_end=timestamp_start, embedding=embedding,
    ))
    db.commit()


_SPARE_AXIS = EMBED_DIM - 1


def unit(axis: int) -> list[float]:
    """The unit vector along `axis` - a stand-in query or document vector."""
    vector = [0.0] * EMBED_DIM
    vector[axis] = 1.0
    return vector


def toward(axis: int, similarity: float) -> list[float]:
    """
    A unit vector whose cosine similarity to unit(axis) is exactly
    `similarity`, so tests can build exact rankings. Leans the rest of the
    way along the last axis, which unit() is never given.
    """
    assert axis != _SPARE_AXIS
    vector = [0.0] * EMBED_DIM
    vector[axis] = similarity
    vector[_SPARE_AXIS] = (1.0 - similarity ** 2) ** 0.5
    return vector


class FakeGeminiTitle:
    """
    client.aio.models.generate_content - the non-streaming call that names a
    chat. Kept apart from FakeGeminiChat's generate_content_stream, so a title
    written concurrently with an answer never takes one of the answer's
    scripted replies.
    """

    def __init__(self):
        self.replies = []
        self.prompts = []

    def reply(self, text, usage=(20, 5)):
        self.replies.append(("text", text, usage))
        return self

    def fail(self, error):
        self.replies.append(("raise", error, None))
        return self

    async def generate_content(self, model, contents, config=None):
        from types import SimpleNamespace

        self.prompts.append(contents)
        if not self.replies:
            raise AssertionError("unscripted Gemini title call")
        kind, value, usage = self.replies.pop(0)
        if kind == "raise":
            raise value
        return SimpleNamespace(
            text=value,
            usage_metadata=SimpleNamespace(prompt_token_count=usage[0], candidates_token_count=usage[1]),
        )

    def install(self, monkeypatch):
        from app.rag import agent_runner

        monkeypatch.setattr(agent_runner.client.aio.models, "generate_content", self.generate_content)
        return self


@pytest.fixture
def api(monkeypatch, db, user):
    """
    A TestClient with two users: `me` (the conftest user) and `other`. Only
    the JWT check (auth._identify) is stubbed - the rest of auth runs for
    real. Import this fixture into a test module to use it.
    """
    from fastapi.testclient import TestClient

    from app.api import auth
    from app.main import app

    me_id, me_email = str(user.id), user.email
    other_id = str(add_user(db, "other@example.com"))
    identities = {"me": (me_id, me_email), "other": (other_id, "other@example.com")}
    monkeypatch.setattr(auth, "_identify", lambda token: identities[token])
    auth.user_row_cache.clear()
    # The fixtures' own session must not sit on a pooled connection that a
    # test measuring the pool would count.
    db.close()
    yield SimpleNamespace(
        client=TestClient(app),
        me={"Authorization": "Bearer me"},
        other={"Authorization": "Bearer other"},
        user_id=me_id,
        other_id=other_id,
    )
    auth.user_row_cache.clear()
