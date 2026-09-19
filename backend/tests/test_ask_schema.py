"""
Ask AI Phase 1 - the schema rules the rest of the feature leans on.

These are enforced by Postgres, not by application code, so they are tested
against the real tables: the one-empty-chat partial unique index, the server
defaults a raw INSERT relies on, the ON DELETE CASCADEs that stand in for
relationship(), and the meeting-date expression index.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import AskAiConversation, AskAiMessage, Meeting, User


def _other_user(db, email="other@example.com") -> User:
    row = User(email=email)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _conversation(db, user, last_message_at=None) -> AskAiConversation:
    row = AskAiConversation(user_id=user.id, last_message_at=last_message_at)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _exchange(db, conversation, user):
    db.add(AskAiMessage(conversation_id=conversation.id, user_id=user.id, role="user", content="Q"))
    db.add(AskAiMessage(conversation_id=conversation.id, user_id=user.id, role="assistant", content="A",
                        tools_used=["list_meetings"]))
    db.commit()


def _count(db, table: str, **where) -> int:
    clause = " AND ".join(f"{col} = :{col}" for col in where) or "true"
    return db.execute(text(f"SELECT count(*) FROM {table} WHERE {clause}"), where).scalar_one()


# ---------------------------------------------------------------------------
# One empty chat per user
# ---------------------------------------------------------------------------

def test_a_second_empty_chat_for_the_same_user_is_rejected(db, user):
    _conversation(db, user)

    with pytest.raises(IntegrityError):
        _conversation(db, user)
    db.rollback()

    assert _count(db, "ask_ai_conversations", user_id=user.id) == 1


def test_each_user_gets_their_own_empty_chat(db, user):
    other = _other_user(db)

    _conversation(db, user)
    _conversation(db, other)

    assert _count(db, "ask_ai_conversations") == 2


def test_a_chat_with_messages_frees_the_empty_slot(db, user):
    first = _conversation(db, user)
    first.last_message_at = datetime.now(timezone.utc)
    db.commit()

    _conversation(db, user)
    _conversation(db, user, last_message_at=datetime.now(timezone.utc))

    assert _count(db, "ask_ai_conversations", user_id=user.id) == 3


def test_insert_on_conflict_returns_the_existing_empty_chat(db, user):
    """
    The race-free "New chat": insert, and on conflict with the partial index
    fall back to the row that is already there. Proves the index can be named
    as a conflict target, which is how Phase 5 will use it.
    """
    sql = text("""
        INSERT INTO ask_ai_conversations (user_id) VALUES (:user_id)
        ON CONFLICT (user_id) WHERE last_message_at IS NULL DO NOTHING
        RETURNING id
    """)
    first = db.execute(sql, {"user_id": user.id}).scalar_one()
    second = db.execute(sql, {"user_id": user.id}).scalar_one_or_none()
    db.commit()

    assert isinstance(first, uuid.UUID)
    assert second is None
    assert _count(db, "ask_ai_conversations", user_id=user.id) == 1


# ---------------------------------------------------------------------------
# Server defaults
# ---------------------------------------------------------------------------

def test_a_raw_insert_gets_an_id_a_title_and_a_created_at(db, user):
    row = db.execute(
        text("INSERT INTO ask_ai_conversations (user_id) VALUES (:user_id) RETURNING id, title, created_at, last_message_at"),
        {"user_id": user.id},
    ).one()
    db.commit()

    assert isinstance(row.id, uuid.UUID)
    assert row.title == "New chat"
    assert row.created_at is not None
    assert row.last_message_at is None


def test_new_columns_on_meetings_and_users_default_to_null(db, user):
    meeting = Meeting(user_id=user.id, meeting_url="https://meet.google.com/abc-defg-hij", platform="google_meet")
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    db.refresh(user)

    assert meeting.summary_embedding is None
    assert user.ask_ai_instructions is None

    meeting.summary_embedding = [0.1] * 768
    user.ask_ai_instructions = "Answer in bullet points."
    db.commit()
    db.refresh(meeting)
    db.refresh(user)

    assert len(meeting.summary_embedding) == 768
    assert user.ask_ai_instructions == "Answer in bullet points."


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------

def test_deleting_a_chat_deletes_its_messages_only(db, user):
    kept = _conversation(db, user, last_message_at=datetime.now(timezone.utc))
    deleted = _conversation(db, user, last_message_at=datetime.now(timezone.utc))
    _exchange(db, kept, user)
    _exchange(db, deleted, user)
    # Read before the delete: after the commit the ORM would try to reload
    # the deleted row to hand back its id.
    kept_id, deleted_id = kept.id, deleted.id

    db.execute(text("DELETE FROM ask_ai_conversations WHERE id = :id"), {"id": deleted_id})
    db.commit()

    assert _count(db, "ask_ai_messages", conversation_id=deleted_id) == 0
    assert _count(db, "ask_ai_messages", conversation_id=kept_id) == 2


def test_deleting_a_user_deletes_their_chats_and_messages(db, user):
    other = _other_user(db)
    mine = _conversation(db, user, last_message_at=datetime.now(timezone.utc))
    theirs = _conversation(db, other, last_message_at=datetime.now(timezone.utc))
    _exchange(db, mine, user)
    _exchange(db, theirs, other)
    user_id, other_id = user.id, other.id

    db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    db.commit()

    assert _count(db, "ask_ai_conversations") == 1
    assert _count(db, "ask_ai_messages") == 2
    assert _count(db, "ask_ai_messages", user_id=other_id) == 2


# ---------------------------------------------------------------------------
# The meeting-date index
# ---------------------------------------------------------------------------

def test_a_date_filter_on_the_meeting_date_expression_uses_the_index(db, user):
    """
    Seq scans are switched off for the query so the planner's choice on a
    near-empty table reflects whether the index *can* serve the expression,
    not whether it is worth it at this size.
    """
    db.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(db.execute(text("""
        EXPLAIN SELECT id FROM meetings
        WHERE user_id = :user_id
          AND coalesce(scheduled_at, created_at) >= :start
          AND coalesce(scheduled_at, created_at) < :end
    """), {
        "user_id": user.id,
        "start": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "end": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }).scalars())
    db.rollback()

    assert "ix_meetings_user_meeting_date" in plan, plan
