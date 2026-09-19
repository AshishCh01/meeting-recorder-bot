import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, MoreHorizontal, Pencil, Plus, Trash2 } from 'lucide-react';
import { groupByRecency } from '../../lib/dateGroups';

// One row: the chat's title as a link, and a menu with Rename (inline) and Delete.
const ConversationRow = ({ conversation, active, onNavigate, onRename, onDeleteRequest }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(conversation.title);
  const menuRef = useRef(null);
  // Set by Escape, so the blur that follows (the input losing focus as it
  // goes away) discards the edit instead of saving it.
  const cancelRef = useRef(false);

  // Close the menu on any click outside it.
  useEffect(() => {
    if (!menuOpen) return undefined;
    const close = (e) => {
      if (!menuRef.current?.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menuOpen]);

  const startEditing = () => {
    cancelRef.current = false;
    setDraft(conversation.title);
    setEditing(true);
    setMenuOpen(false);
  };

  const finishEditing = async (save) => {
    setEditing(false);
    const title = draft.trim();
    if (save && title && title !== conversation.title) {
      try {
        await onRename(conversation.id, title);
      } catch (err) {
        console.error('Rename failed', err);
      }
    }
  };

  if (editing) {
    return (
      <input
        autoFocus
        value={draft}
        maxLength={100}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => finishEditing(!cancelRef.current)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') cancelRef.current = true;
          if (e.key === 'Enter' || e.key === 'Escape') e.currentTarget.blur();
        }}
        className="w-full h-9 px-2.5 border border-brand-blue rounded-lg bg-surface text-sm text-brand-dark focus:outline-none focus:ring-2 focus:ring-brand-blue/20"
        aria-label="Chat title"
      />
    );
  }

  return (
    <div
      className={`group relative flex items-center rounded-lg transition-colors ${
        active ? 'bg-status-done-bg' : 'hover:bg-surface-hover'
      }`}
    >
      <Link
        to={`/ask/${conversation.id}`}
        onClick={onNavigate}
        className={`flex-1 min-w-0 px-2.5 py-2 text-sm truncate ${
          active ? 'font-semibold text-status-done-fg' : 'text-body group-hover:text-brand-dark'
        }`}
        title={conversation.title}
      >
        {conversation.title}
      </Link>
      <div ref={menuRef} className="relative flex-none">
        <button
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          className={`p-1.5 mr-1 rounded-md text-faint hover:text-body transition-opacity ${
            menuOpen ? 'opacity-100' : 'opacity-100 lg:opacity-0 lg:group-hover:opacity-100 focus:opacity-100'
          }`}
          aria-label="Chat options"
        >
          <MoreHorizontal className="w-4 h-4" />
        </button>
        {menuOpen && (
          <div className="absolute right-1 top-full mt-1 z-20 w-36 bg-surface border border-border-strong rounded-xl shadow-lg py-1">
            <button
              type="button"
              onClick={startEditing}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm text-body hover:bg-surface-hover hover:text-brand-dark"
            >
              <Pencil className="w-3.5 h-3.5" /> Rename
            </button>
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                onDeleteRequest(conversation);
              }}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-600 dark:text-red-400 hover:bg-surface-hover"
            >
              <Trash2 className="w-3.5 h-3.5" /> Delete
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

// The Ask AI chat list: New chat, the user's chats grouped by recency (more
// load as the list scrolls), and Delete all at the bottom.
export const ConversationList = ({
  conversations,
  activeId,
  creating,
  onNewChat,
  onNavigate,
  onRename,
  onDeleteRequest,
  onDeleteAllRequest,
}) => {
  const { items, loading, error, hasMore, loadMore } = conversations;
  const sentinelRef = useRef(null);

  // Infinite scroll: fetch the next page once the end of the list is in view.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel || !hasMore) return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) loadMore();
    });
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, loadMore]);

  const groups = groupByRecency(items, (c) => c.last_message_at);

  return (
    <div className="flex flex-col h-full">
      <div className="flex-none p-3">
        <button
          type="button"
          onClick={onNewChat}
          disabled={creating}
          className="w-full flex items-center justify-center gap-2 h-10 rounded-xl bg-brand-blue text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
          New chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {loading ? (
          <div className="flex justify-center py-6">
            <Loader2 className="w-5 h-5 animate-spin text-faint" />
          </div>
        ) : error ? (
          <p className="px-2.5 py-4 text-sm text-red-600 dark:text-red-400">{error}</p>
        ) : items.length === 0 ? (
          <p className="px-2.5 py-4 text-sm text-muted">Your chats will appear here.</p>
        ) : (
          <div className="flex flex-col gap-4">
            {groups.map((group) => (
              <div key={group.label}>
                <div className="px-2.5 pb-1 text-[11px] font-bold uppercase tracking-wide text-faint">
                  {group.label}
                </div>
                <div className="flex flex-col gap-0.5">
                  {group.items.map((c) => (
                    <ConversationRow
                      key={c.id}
                      conversation={c}
                      active={c.id === activeId}
                      onNavigate={onNavigate}
                      onRename={onRename}
                      onDeleteRequest={onDeleteRequest}
                    />
                  ))}
                </div>
              </div>
            ))}
            {hasMore && (
              <div ref={sentinelRef} className="flex justify-center py-2">
                <Loader2 className="w-4 h-4 animate-spin text-faint" />
              </div>
            )}
          </div>
        )}
      </div>

      {items.length > 0 && (
        <div className="flex-none p-3 border-t border-line">
          <button
            type="button"
            onClick={onDeleteAllRequest}
            className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-sm text-faint hover:text-red-600 dark:hover:text-red-400 hover:bg-surface-hover transition-colors"
          >
            <Trash2 className="w-4 h-4" /> Delete all chats
          </button>
        </div>
      )}
    </div>
  );
};
