"""
Ask AI Phase 4 - stored conversations (app/ask_ai/agent/history.py).

The rules the chat list and the "New chat" button depend on: at most one
empty chat per user, which empty chats never leave the list for, and every
read and write scoped to its owner. (The HTTP routes join this file in Phase 5.)
"""
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.ask_ai.agent import history
from app.config import settings
from app.db.database import SessionLocal

from tests.ask_ai_fixtures import add_user


def count(table: str, **where) -> int:
    clause = " AND ".join(f"{col} = :{col}" for col in where) or "true"
    db = SessionLocal()
    try:
        return db.execute(text(f"SELECT count(*) FROM {table} WHERE {clause}"), where).scalar_one()
    finally:
        db.close()


def used_chat(user_id, *exchanges):
    """A chat with the given (question, answer) pairs saved in it."""
    chat = history.get_or_create_empty_conversation(user_id)
    for question, answer in exchanges:
        assert history.save_exchange(chat.id, user_id, question, answer, ["list_meetings"])
    return chat


# ---------------------------------------------------------------------------
# New chat
# ---------------------------------------------------------------------------

def test_new_chat_reuses_the_empty_one(user):
    first = history.get_or_create_empty_conversation(str(user.id))
    second = history.get_or_create_empty_conversation(str(user.id))

    assert first.id == second.id
    assert (first.title, first.last_message_at) == ("New chat", None)


def test_once_the_chat_has_an_exchange_new_chat_makes_another(user):
    first = used_chat(str(user.id), ("Q", "A"))

    second = history.get_or_create_empty_conversation(str(user.id))

    assert second.id != first.id
    assert count("ask_ai_conversations", user_id=user.id) == 2


def test_concurrent_new_chats_still_make_one_empty_chat(user):
    ids, errors = [], []
    barrier = threading.Barrier(8)

    def click():
        try:
            barrier.wait()
            ids.append(history.get_or_create_empty_conversation(str(user.id)).id)
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=click) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(set(ids)) == 1
    assert count("ask_ai_conversations", user_id=user.id) == 1


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_empty_chats_are_never_listed(user):
    history.get_or_create_empty_conversation(str(user.id))

    assert history.list_conversations(str(user.id)) == []


def test_chats_are_listed_most_recently_used_first_and_page_by_last_message(user):
    user_id = str(user.id)
    chats = [used_chat(user_id, (f"Q{i}", f"A{i}")) for i in range(3)]
    db = SessionLocal()
    try:
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for i, chat in enumerate(chats):
            db.execute(text("UPDATE ask_ai_conversations SET last_message_at = :t WHERE id = :id"),
                       {"t": base + timedelta(days=i), "id": chat.id})
        db.commit()
    finally:
        db.close()

    listed = history.list_conversations(user_id)
    assert [c.id for c in listed] == [chats[2].id, chats[1].id, chats[0].id]

    page_one = history.list_conversations(user_id, limit=2)
    page_two = history.list_conversations(user_id, limit=2, before=page_one[-1].last_message_at)
    assert [c.id for c in page_one + page_two] == [c.id for c in listed]


def test_another_users_chats_are_not_listed(db, user):
    used_chat(str(add_user(db, "other@example.com")), ("Q", "A"))

    assert history.list_conversations(str(user.id)) == []


# ---------------------------------------------------------------------------
# Saving and loading
# ---------------------------------------------------------------------------

def test_an_exchange_is_both_messages_and_last_message_at_in_one_commit(user):
    chat = history.get_or_create_empty_conversation(str(user.id))

    assert history.save_exchange(chat.id, str(user.id), "When is the launch?", "On the 14th.", ["list_meetings"])

    messages = history.load_messages(chat.id, str(user.id))
    assert [(m.role, m.content, m.tools_used) for m in messages] == [
        ("user", "When is the launch?", None),
        ("assistant", "On the 14th.", ["list_meetings"]),
    ]
    assert history.get_owned_conversation(chat.id, str(user.id)).last_message_at is not None


def test_saving_into_a_deleted_or_unowned_chat_stores_nothing(db, user):
    chat = history.get_or_create_empty_conversation(str(user.id))
    other = str(add_user(db, "other@example.com"))

    assert history.save_exchange(chat.id, other, "Q", "A", []) is False
    assert history.save_exchange(uuid.uuid4(), str(user.id), "Q", "A", []) is False
    assert count("ask_ai_messages") == 0
    assert history.get_owned_conversation(chat.id, str(user.id)).last_message_at is None


def test_the_thread_for_the_model_is_the_last_turns_oldest_first(user, monkeypatch):
    monkeypatch.setattr(settings, "ask_ai_history_turn_limit", 4)
    chat = used_chat(str(user.id), ("Q1", "A1"), ("Q2", "A2"), ("Q3", "A3"))

    thread = history.load_thread(chat.id, str(user.id))

    assert [(c.role, c.parts[0].text) for c in thread] == [("user", "Q2"), ("model", "A2"), ("user", "Q3"), ("model", "A3")]


def test_another_users_chat_reads_as_missing(db, user):
    chat = used_chat(str(user.id), ("Q", "A"))
    other = str(add_user(db, "other@example.com"))

    assert history.get_owned_conversation(chat.id, other) is None
    assert history.load_messages(chat.id, other) == []
    assert history.set_title(chat.id, other, "Hijacked") is False
    assert history.get_owned_conversation(chat.id, str(user.id)).title == "New chat"


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------

def test_deleting_a_chat_deletes_its_messages(db, user):
    chat = used_chat(str(user.id), ("Q", "A"))
    kept = used_chat(str(user.id), ("Q", "A"))

    assert history.delete_conversation(chat.id, str(user.id)) is True

    assert count("ask_ai_messages", conversation_id=chat.id) == 0
    assert count("ask_ai_messages", conversation_id=kept.id) == 2


def test_only_the_owner_can_delete_a_chat(db, user):
    chat = used_chat(str(user.id), ("Q", "A"))
    other = str(add_user(db, "other@example.com"))

    assert history.delete_conversation(chat.id, other) is False
    assert history.get_owned_conversation(chat.id, str(user.id)) is not None


def test_delete_all_deletes_only_the_users_chats_and_returns_their_ids(db, user):
    mine = [used_chat(str(user.id), ("Q", "A")).id, history.get_or_create_empty_conversation(str(user.id)).id]
    other = str(add_user(db, "other@example.com"))
    theirs = used_chat(other, ("Q", "A"))

    assert sorted(history.delete_all_conversations(str(user.id))) == sorted(mine)

    assert count("ask_ai_conversations", user_id=user.id) == 0
    assert history.get_owned_conversation(theirs.id, other) is not None
    assert count("ask_ai_messages", user_id=uuid.UUID(other)) == 2
