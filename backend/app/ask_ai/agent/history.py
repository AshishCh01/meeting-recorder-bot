"""
Ask AI conversations and messages: create, load, save, rename, delete.

Every function is blocking (SQLAlchemy) and opens its own short session, so
the async service calls them through asyncio.to_thread. Every one is scoped to
the user_id it is given: a conversation someone else owns behaves exactly like
one that doesn't exist.
"""

from datetime import datetime

from google.genai import types
from sqlalchemy import delete, text

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import AskAiConversation, AskAiMessage

# "INSERT ... ON CONFLICT" against the partial unique index
# ux_ask_ai_conversations_one_empty_per_user: at most one empty chat per user.
# Two tabs clicking "New chat" at once both land on the same row.
_INSERT_EMPTY = text("""
    INSERT INTO ask_ai_conversations (user_id) VALUES (:user_id)
    ON CONFLICT (user_id) WHERE last_message_at IS NULL DO NOTHING
""")


def get_or_create_empty_conversation(user_id: str) -> AskAiConversation:
    """
    The user's empty chat - the existing one if there is one, else a new one.

    Retried because the empty chat can stop being empty between the insert
    (which did nothing, since it existed) and the select (a question landed in
    it from another tab); the next insert then succeeds.
    """
    for _ in range(3):
        db = SessionLocal()
        try:
            db.execute(_INSERT_EMPTY, {"user_id": user_id})
            db.commit()
            row = (
                db.query(AskAiConversation)
                .filter(AskAiConversation.user_id == user_id, AskAiConversation.last_message_at.is_(None))
                .first()
            )
            if row is not None:
                db.expunge(row)
                return row
        finally:
            db.close()
    raise RuntimeError("could not get or create an empty Ask AI conversation")


def get_owned_conversation(conversation_id, user_id: str) -> AskAiConversation | None:
    db = SessionLocal()
    try:
        row = (
            db.query(AskAiConversation)
            .filter(AskAiConversation.id == conversation_id, AskAiConversation.user_id == user_id)
            .first()
        )
        if row is not None:
            db.expunge(row)
        return row
    finally:
        db.close()


def list_conversations(user_id: str, limit: int = 30, before: datetime | None = None) -> list[AskAiConversation]:
    """
    The user's chats that have at least one exchange, most recently used
    first. `before` is the last_message_at of the last chat on the previous
    page. Empty chats are never listed.
    """
    db = SessionLocal()
    try:
        query = db.query(AskAiConversation).filter(
            AskAiConversation.user_id == user_id,
            AskAiConversation.last_message_at.isnot(None),
        )
        if before is not None:
            query = query.filter(AskAiConversation.last_message_at < before)
        rows = query.order_by(AskAiConversation.last_message_at.desc()).limit(limit).all()
        for row in rows:
            db.expunge(row)
        return rows
    finally:
        db.close()


def load_messages(conversation_id, user_id: str, limit: int = 200) -> list[AskAiMessage]:
    """The chat's most recent `limit` messages, oldest first - for the page."""
    db = SessionLocal()
    try:
        rows = (
            db.query(AskAiMessage)
            .filter(AskAiMessage.conversation_id == conversation_id, AskAiMessage.user_id == user_id)
            .order_by(AskAiMessage.id.desc())
            .limit(limit)
            .all()
        )
        for row in rows:
            db.expunge(row)
    finally:
        db.close()
    rows.reverse()
    return rows


def load_thread(conversation_id, user_id: str) -> list[types.Content]:
    """
    Rebuilds a cold session's history from the stored chat: its last
    ask_ai_history_turn_limit text turns, oldest first. Mirrors
    chat_service._load_thread - text turns only; tool round-trips are re-run
    rather than replayed (see the ChatMessage docstring).
    """
    rows = load_messages(conversation_id, user_id, limit=settings.ask_ai_history_turn_limit)
    return [
        types.Content(
            role="model" if row.role == "assistant" else "user",
            parts=[types.Part.from_text(text=row.content)],
        )
        for row in rows
    ]


def save_exchange(conversation_id, user_id: str, question: str, answer: str, tools_used: list[str]) -> bool:
    """
    Stores one question/answer pair and marks the chat as used, in one commit,
    so a chat can never be read back with a question and no answer, or be
    non-empty without messages.

    Returns False, storing nothing, if the chat no longer exists (deleted
    while the answer streamed) or isn't this user's.
    """
    db = SessionLocal()
    try:
        touched = (
            db.query(AskAiConversation)
            .filter(AskAiConversation.id == conversation_id, AskAiConversation.user_id == user_id)
            .update({AskAiConversation.last_message_at: text("now()")}, synchronize_session=False)
        )
        if not touched:
            db.rollback()
            return False
        db.add(AskAiMessage(conversation_id=conversation_id, user_id=user_id, role="user", content=question))
        db.add(AskAiMessage(
            conversation_id=conversation_id, user_id=user_id, role="assistant", content=answer,
            tools_used=tools_used or None,
        ))
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def set_title(conversation_id, user_id: str, title: str) -> bool:
    db = SessionLocal()
    try:
        updated = (
            db.query(AskAiConversation)
            .filter(AskAiConversation.id == conversation_id, AskAiConversation.user_id == user_id)
            .update({AskAiConversation.title: title}, synchronize_session=False)
        )
        db.commit()
        return bool(updated)
    finally:
        db.close()


def delete_conversation(conversation_id, user_id: str) -> bool:
    """Deletes one chat; its messages go with it (ON DELETE CASCADE)."""
    db = SessionLocal()
    try:
        deleted = (
            db.query(AskAiConversation)
            .filter(AskAiConversation.id == conversation_id, AskAiConversation.user_id == user_id)
            .delete(synchronize_session=False)
        )
        db.commit()
        return bool(deleted)
    finally:
        db.close()


def delete_all_conversations(user_id: str) -> list:
    """Deletes every chat the user has, returning their ids (for evicting cached sessions)."""
    db = SessionLocal()
    try:
        # One statement, so the ids returned are exactly the chats deleted.
        ids = list(db.execute(
            delete(AskAiConversation)
            .where(AskAiConversation.user_id == user_id)
            .returning(AskAiConversation.id)
        ).scalars())
        db.commit()
        return ids
    finally:
        db.close()
