import asyncio
from collections import OrderedDict
from google.genai import types


class LRUSessionCache:
    def __init__(self, capacity: int = 500):
        self.capacity = capacity
        # Maps key -> (asyncio.Lock(), list[types.Content])
        self.cache: OrderedDict[str, tuple[asyncio.Lock, list[types.Content]]] = OrderedDict()
        self.lock = asyncio.Lock()

    async def get_session(self, user_id: str, meeting_id: str, session_id: str) -> tuple[asyncio.Lock, list[types.Content]]:
        key = f"{user_id}:{meeting_id}:{session_id}"
        async with self.lock:
            if key not in self.cache:
                if len(self.cache) >= self.capacity:
                    self.cache.popitem(last=False)  # Remove oldest (FIFO/LRU)
                self.cache[key] = (asyncio.Lock(), [])
            else:
                self.cache.move_to_end(key)
            return self.cache[key]

    async def evict(self, key: str) -> None:
        """
        Drops one session's in-memory history, so the next get_session for
        the key starts empty. A no-op for a key that isn't cached. Used when
        the stored conversation behind a session is deleted: the history
        would otherwise keep answering from turns the user just removed.
        """
        async with self.lock:
            self.cache.pop(key, None)
