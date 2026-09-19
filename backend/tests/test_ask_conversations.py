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


# ---------------------------------------------------------------------------
# The HTTP routes (Phase 5)
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app.ask_ai.agent import service  # noqa: E402

from tests.ask_ai_fixtures import api  # noqa: E402,F401 - fixture


def test_new_chat_returns_the_same_empty_chat_until_it_is_used(api):
    first = api.client.post("/ask/conversations", headers=api.me).json()
    again = api.client.post("/ask/conversations", headers=api.me).json()

    assert first == again
    assert first["title"] == "New chat" and first["last_message_at"] is None

    history.save_exchange(first["id"], api.user_id, "Q", "A", [])
    fresh = api.client.post("/ask/conversations", headers=api.me).json()
    assert fresh["id"] != first["id"]


def test_the_list_holds_used_chats_only_newest_first(api):
    older = used_chat(api.user_id, ("Q1", "A1"))
    newer = used_chat(api.user_id, ("Q2", "A2"))
    api.client.post("/ask/conversations", headers=api.me)  # an empty chat, not listed

    listed = api.client.get("/ask/conversations", headers=api.me).json()

    assert [c["id"] for c in listed] == [str(newer.id), str(older.id)]
    assert all(c["last_message_at"] for c in listed)
    assert api.client.get("/ask/conversations?limit=1", headers=api.me).json()[0]["id"] == str(newer.id)
    page_two = api.client.get(
        "/ask/conversations", params={"before": listed[0]["last_message_at"]}, headers=api.me,
    ).json()
    assert [c["id"] for c in page_two] == [str(older.id)]


def test_a_chats_messages_come_back_oldest_first(api):
    new = api.client.post("/ask/conversations", headers=api.me).json()
    assert api.client.get(f"/ask/conversations/{new['id']}/messages", headers=api.me).json() == {
        "id": new["id"], "title": "New chat", "messages": [],
    }

    history.save_exchange(new["id"], api.user_id, "When is the launch?", "On the 14th.", ["list_meetings"])
    body = api.client.get(f"/ask/conversations/{new['id']}/messages", headers=api.me).json()

    assert [(m["role"], m["content"], m["tools_used"]) for m in body["messages"]] == [
        ("user", "When is the launch?", []),
        ("assistant", "On the 14th.", ["list_meetings"]),
    ]
    assert all(m["created_at"] for m in body["messages"])


def test_rename(api):
    chat = used_chat(api.user_id, ("Q", "A"))

    response = api.client.patch(f"/ask/conversations/{chat.id}", json={"title": "  Launch   plans "}, headers=api.me)

    assert response.status_code == 200
    assert response.json()["title"] == "Launch plans"
    assert history.get_owned_conversation(chat.id, api.user_id).title == "Launch plans"


@pytest.mark.parametrize("title", ["", "   ", "x" * 101])
def test_a_blank_or_overlong_title_is_rejected(api, title):
    chat = used_chat(api.user_id, ("Q", "A"))

    assert api.client.patch(f"/ask/conversations/{chat.id}", json={"title": title}, headers=api.me).status_code == 422


def test_delete_removes_the_messages_and_the_cached_history(api):
    chat = used_chat(api.user_id, ("Q", "A"))
    key = service.session_key(api.user_id, chat.id)
    asyncio.run(service._session_cache.get_session(*service._session_parts(api.user_id, chat.id)))
    assert key in service._session_cache.cache

    response = api.client.delete(f"/ask/conversations/{chat.id}", headers=api.me)

    assert response.json() == {"status": "deleted"}
    assert count("ask_ai_messages", conversation_id=chat.id) == 0
    assert key not in service._session_cache.cache, "a deleted chat's history could reach a later answer"
    assert api.client.delete(f"/ask/conversations/{chat.id}", headers=api.me).status_code == 404


def test_delete_all_evicts_every_cached_history(api):
    chats = [used_chat(api.user_id, ("Q", "A")) for _ in range(2)]
    for chat in chats:
        asyncio.run(service._session_cache.get_session(*service._session_parts(api.user_id, chat.id)))

    assert api.client.delete("/ask/conversations", headers=api.me).json() == {"status": "deleted", "count": 2}
    assert not any(service.session_key(api.user_id, c.id) in service._session_cache.cache for c in chats)


@pytest.mark.parametrize("method, path", [
    ("post", "/ask/conversations"), ("get", "/ask/conversations"), ("delete", "/ask/conversations"),
])
def test_every_route_needs_a_login(api, method, path):
    assert getattr(api.client, method)(path).status_code in (401, 403)
