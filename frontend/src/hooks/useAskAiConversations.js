import { useCallback, useEffect, useRef, useState } from 'react';
import api from '../lib/api';

const PAGE_SIZE = 30;

const byId = (items) => new Map(items.map((c) => [c.id, c]));

// The Ask AI chat list: the user's chats that have at least one answer, most
// recently used first, paged with the backend's `before` cursor. Empty chats
// never come back from the API, so a new chat joins the list only once it
// has been answered (refreshHead).
export function useAskAiConversations() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(null);
  const loadingMoreRef = useRef(false);

  const fetchPage = (before) =>
    api.get('/ask/conversations', { params: { limit: PAGE_SIZE, ...(before ? { before } : {}) } })
      .then(({ data }) => data);

  useEffect(() => {
    let cancelled = false;
    fetchPage()
      .then((page) => {
        if (cancelled) return;
        setItems(page);
        setHasMore(page.length === PAGE_SIZE);
      })
      .catch((err) => {
        console.error('Failed to load chats', err);
        if (!cancelled) setError('Couldn’t load your chats.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !hasMore || !items.length) return;
    loadingMoreRef.current = true;
    try {
      const page = await fetchPage(items[items.length - 1].last_message_at);
      setItems((prev) => {
        const seen = byId(prev);
        return [...prev, ...page.filter((c) => !seen.has(c.id))];
      });
      setHasMore(page.length === PAGE_SIZE);
    } catch (err) {
      console.error('Failed to load more chats', err);
    } finally {
      loadingMoreRef.current = false;
    }
  }, [hasMore, items]);

  // Re-reads the first page after an answer, so a chat's first answer puts
  // it at the top and a used chat moves back up - with the server's own
  // title and time, rather than a guess at what was saved.
  const refreshHead = useCallback(async () => {
    try {
      const page = await fetchPage();
      const fresh = byId(page);
      setItems((prev) => [...page, ...prev.filter((c) => !fresh.has(c.id))]);
    } catch (err) {
      console.error('Failed to refresh chats', err);
    }
  }, []);

  const setTitle = useCallback((id, title) => {
    setItems((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)));
  }, []);

  // "New chat": the user's one empty chat, created if needed.
  const create = useCallback(async () => {
    const { data } = await api.post('/ask/conversations');
    return data;
  }, []);

  const rename = useCallback(async (id, title) => {
    const { data } = await api.patch(`/ask/conversations/${id}`, { title });
    setTitle(id, data.title);
    return data;
  }, [setTitle]);

  const remove = useCallback(async (id) => {
    try {
      await api.delete(`/ask/conversations/${id}`);
    } catch (err) {
      // Already gone - deleted in another tab. The row goes either way.
      if (err.response?.status !== 404) throw err;
    }
    setItems((prev) => prev.filter((c) => c.id !== id));
  }, []);

  const removeAll = useCallback(async () => {
    await api.delete('/ask/conversations');
    setItems([]);
    setHasMore(false);
  }, []);

  return { items, loading, error, hasMore, loadMore, refreshHead, setTitle, create, rename, remove, removeAll };
}
