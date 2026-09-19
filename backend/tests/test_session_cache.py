"""
Ask AI Phase 0 - LRUSessionCache.evict().

Deleting a stored conversation has to drop its in-memory history too, or the
next question in that session would still be answered from the deleted turns.
"""

import asyncio

from google.genai import types

from app.rag.session_cache import LRUSessionCache


def _turn(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part.from_text(text=text)])


def test_evict_drops_an_existing_session():
    async def run():
        cache = LRUSessionCache(capacity=10)
        _, history = await cache.get_session("u1", "m1", "s1")
        history.append(_turn("When is the launch?"))
        other_lock, other = await cache.get_session("u1", "m1", "s2")
        other.append(_turn("Who owns the budget?"))

        await cache.evict("u1:m1:s1")

        return cache, other_lock, other

    cache, other_lock, other = asyncio.run(run())

    assert "u1:m1:s1" not in cache.cache
    # Only the named session goes.
    assert cache.cache["u1:m1:s2"] == (other_lock, other)
    assert len(other) == 1


def test_evict_is_a_no_op_for_a_missing_key():
    async def run():
        cache = LRUSessionCache(capacity=10)
        lock, history = await cache.get_session("u1", "m1", "s1")
        await cache.evict("u1:m1:never-created")
        return cache, lock, history

    cache, lock, history = asyncio.run(run())

    assert list(cache.cache) == ["u1:m1:s1"]
    assert cache.cache["u1:m1:s1"] == (lock, history)


def test_a_session_fetched_after_evict_starts_empty():
    async def run():
        cache = LRUSessionCache(capacity=10)
        old_lock, old_history = await cache.get_session("u1", "m1", "s1")
        old_history.append(_turn("When is the launch?"))

        await cache.evict("u1:m1:s1")
        new_lock, new_history = await cache.get_session("u1", "m1", "s1")
        return old_lock, old_history, new_lock, new_history

    old_lock, old_history, new_lock, new_history = asyncio.run(run())

    assert new_history == []
    assert new_history is not old_history
    assert new_lock is not old_lock
