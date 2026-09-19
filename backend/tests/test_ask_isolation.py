"""
Ask AI - one user can never reach another's meetings.

Phase 3 covers the tools: every one is scoped to the user_id the service
binds, and a meeting id the user doesn't own looks exactly like one that
doesn't exist. (The chat routes' ownership checks join this file in Phase 5.)
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.ask_ai import tools
from app.ask_ai.tools import search as search_module
from app.ask_ai.tools.common import NOT_FOUND

from tests.ask_ai_fixtures import KOLKATA, add_chunk, add_meeting, add_user, toward, unit

AUG_1 = datetime(2026, 8, 1, 4, 30, tzinfo=timezone.utc)
AXIS = 1


@pytest.fixture
def two_users(db, monkeypatch):
    """A and B each own one indexed, completed meeting that matches everything."""
    monkeypatch.setattr(search_module, "embed_query", lambda query, provider="gemini", **kw: unit(AXIS))
    a = add_user(db, "a@example.com")
    b = add_user(db, "b@example.com")
    a_meeting = add_meeting(db, a, when=AUG_1, title="A's meeting", summary_embedding=toward(AXIS, 0.5),
                            action_items=[{"item": "A's task", "owner": "A"}])
    b_meeting = add_meeting(db, b, when=AUG_1, title="B's meeting", summary_embedding=toward(AXIS, 0.9),
                            action_items=[{"item": "B's task", "owner": "B"}])
    add_chunk(db, a_meeting, "A said something.", toward(AXIS, 0.5))
    add_chunk(db, b_meeting, "B said something better.", toward(AXIS, 0.9))
    return {"a": str(a), "b": str(b), "a_meeting": str(a_meeting), "b_meeting": str(b_meeting)}


def as_user(user_id):
    return {"user_id": user_id, "tz_name": KOLKATA}


def test_list_meetings_shows_only_the_users_own(two_users):
    result = tools.list_meetings(**as_user(two_users["a"]))

    assert [m["id"] for m in result["meetings"]] == [two_users["a_meeting"]]


def test_find_meetings_by_topic_ranks_only_the_users_own(two_users):
    result = tools.find_meetings_by_topic(**as_user(two_users["a"]), query="anything")

    assert [m["id"] for m in result["meetings"]] == [two_users["a_meeting"]]


def test_search_across_meetings_searches_only_the_users_own(two_users):
    result = tools.search_across_meetings(**as_user(two_users["a"]), query="anything")

    assert [m["content"] for m in result["matches"]] == ["A said something."]


@pytest.mark.parametrize("tool", [tools.get_meeting_details, tools.get_meeting_action_items])
def test_another_users_meeting_id_looks_exactly_like_a_missing_one(two_users, tool):
    theirs = tool(**as_user(two_users["a"]), meeting_id=two_users["b_meeting"])
    missing = tool(**as_user(two_users["a"]), meeting_id=str(uuid.uuid4()))
    garbage = tool(**as_user(two_users["a"]), meeting_id="not-a-uuid'; DROP TABLE meetings; --")

    assert theirs == missing == garbage == NOT_FOUND


@pytest.mark.parametrize("tool", [tools.get_meeting_details, tools.get_meeting_action_items])
def test_the_owner_can_read_their_own_meeting(two_users, tool):
    result = tool(**as_user(two_users["a"]), meeting_id=two_users["a_meeting"])

    assert result["id"] == two_users["a_meeting"]
    assert result["title"] == "A's meeting"


def test_action_items_come_back_with_the_meeting(two_users):
    result = tools.get_meeting_action_items(**as_user(two_users["a"]), meeting_id=two_users["a_meeting"])

    assert result["action_items"] == [{"item": "A's task", "owner": "A"}]
    assert result["date"] == "Sat 1 Aug 2026, 10:00"


def test_a_meeting_that_is_not_completed_is_not_found(db, two_users):
    pending = add_meeting(db, uuid.UUID(two_users["a"]), when=AUG_1, status="transcribing")

    assert tools.get_meeting_details(**as_user(two_users["a"]), meeting_id=str(pending)) == NOT_FOUND


def test_details_return_the_whole_summary(db, two_users):
    long_summary = "detail " * 200
    meeting = add_meeting(db, uuid.UUID(two_users["a"]), when=AUG_1, summary=long_summary, key_points=["One", "Two"])

    result = tools.get_meeting_details(**as_user(two_users["a"]), meeting_id=str(meeting))

    assert result["summary"] == long_summary, "details are not clipped like list_meetings' summaries"
    assert result["key_points"] == ["One", "Two"]
    assert set(result) == {"id", "title", "date", "platform", "duration_minutes", "summary", "key_points", "conclusion"}
