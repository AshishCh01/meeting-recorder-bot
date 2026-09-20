import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Loader2, MessagesSquare, Plus, Sparkles, X } from 'lucide-react';
import { Layout } from '../components/Layout';
import { ChatInterface } from '../components/ChatInterface';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ConversationList } from '../components/ask/ConversationList';
import { askMarkdownComponents } from '../components/ask/askMarkdown';
import { useAskAiChat } from '../hooks/useAskAiChat';
import { useAskAiConversations } from '../hooks/useAskAiConversations';

// Day-first, like the dates the assistant is told to expect ("15/09/2026").
const dayFirst = (date) =>
  `${String(date.getDate()).padStart(2, '0')}/${String(date.getMonth() + 1).padStart(2, '0')}/${date.getFullYear()}`;

const starterPrompts = () => {
  const twoDaysAgo = new Date();
  twoDaysAgo.setDate(twoDaysAgo.getDate() - 2);
  return [
    'What did I discuss yesterday?',
    "Summarize last week's meetings",
    'Which meeting discussed pricing?',
    `Action items from ${dayFirst(twoDaysAgo)}`,
  ];
};

const Centered = ({ children }) => (
  <div className="flex-1 flex flex-col items-center justify-center gap-4 p-6 text-center">{children}</div>
);

// Top-aligned rather than vertically centred. Centring inside a scroll
// container is what clipped the last row of starter prompts on a short
// viewport: the overflow went off the top, where it could not be scrolled to.
const EmptyChat = ({ onPrompt, disabled }) => (
  <div className="flex flex-col items-center gap-6 pb-4 pt-[6vh] text-center">
    <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-soft">
      <Sparkles className="h-6 w-6 text-accent-ink" />
    </div>
    <div>
      <h2 className="text-xl font-extrabold tracking-tight text-brand-dark">Ask anything about your meetings</h2>
      <p className="mt-1.5 max-w-md text-sm text-muted">
        Search by date, topic or title. Answers link back to the meetings they came from.
      </p>
    </div>
    <div className="grid w-full max-w-lg gap-2 sm:grid-cols-2">
      {starterPrompts().map((prompt) => (
        <button
          key={prompt}
          type="button"
          onClick={() => onPrompt(prompt)}
          disabled={disabled}
          className="rounded-xl border border-line bg-surface px-3.5 py-3 text-left text-sm font-medium text-body transition-colors hover:border-brand-blue hover:text-brand-dark disabled:opacity-50"
        >
          {prompt}
        </button>
      ))}
    </div>
  </div>
);

export const AskAI = () => {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const conversations = useAskAiConversations();
  const { create, remove, removeAll, rename, setTitle, refreshHead } = conversations;

  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  // A conversation, or 'all' for Delete all chats.
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const chat = useAskAiChat(conversationId, {
    onTitle: setTitle,
    // A chat's first answer puts it in the list; a later one moves it up.
    onAnswered: refreshHead,
  });

  // Every chat has its own id and URL before the first question: "New chat"
  // gets (or reuses) the user's empty chat and opens it in place.
  const openNewChat = useCallback(async () => {
    setCreating(true);
    setCreateError(null);
    try {
      const created = await create();
      setDrawerOpen(false);
      navigate(`/ask/${created.id}`, { replace: true });
    } catch (err) {
      console.error('Failed to start a chat', err);
      setCreateError('Couldn’t start a new chat. Please try again.');
    } finally {
      setCreating(false);
    }
  }, [create, navigate]);

  // /ask on its own goes straight to a chat. Guarded so StrictMode's double
  // effect in development doesn't ask twice (harmless - the API returns the
  // same empty chat - but pointless).
  const startedRef = useRef(false);
  useEffect(() => {
    if (conversationId) {
      startedRef.current = false;
      return;
    }
    if (!startedRef.current) {
      startedRef.current = true;
      openNewChat();
    }
  }, [conversationId, openNewChat]);

  const confirmDelete = async () => {
    setDeleting(true);
    try {
      if (deleteTarget === 'all') {
        await removeAll();
        await openNewChat();
      } else {
        await remove(deleteTarget.id);
        if (deleteTarget.id === conversationId) await openNewChat();
      }
      setDeleteTarget(null);
    } catch (err) {
      console.error('Delete failed', err);
      alert('Failed to delete. Please try again.');
    } finally {
      setDeleting(false);
    }
  };

  useEffect(() => {
    if (!drawerOpen) return undefined;
    const onKey = (e) => e.key === 'Escape' && setDrawerOpen(false);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [drawerOpen]);

  const requestDelete = (target) => {
    setDrawerOpen(false);
    setDeleteTarget(target);
  };

  const list = (
    <ConversationList
      conversations={conversations}
      activeId={conversationId}
      creating={creating}
      onNewChat={openNewChat}
      onNavigate={() => setDrawerOpen(false)}
      onRename={rename}
      onDeleteRequest={requestDelete}
      onDeleteAllRequest={() => requestDelete('all')}
    />
  );

  const header = (
    <div className="flex flex-none items-center gap-2.5 border-b border-line px-4 py-3.5 tablet:px-5">
      <button
        type="button"
        onClick={() => setDrawerOpen(true)}
        className="-ml-1.5 rounded-lg p-1.5 text-muted transition-colors hover:bg-tint-2 hover:text-brand-dark wide:hidden"
        aria-label="Show chats"
      >
        <MessagesSquare className="h-5 w-5" />
      </button>
      <Sparkles className="hidden h-5 w-5 flex-none text-brand-blue wide:block" />
      <div className="min-w-0">
        <div className="text-[15px] font-extrabold text-brand-dark tracking-tight truncate">{chat.title}</div>
        <div className="text-xs text-muted">Answers cite your meetings</div>
      </div>
    </div>
  );

  let main;
  if (!conversationId || (creating && chat.state !== 'ready')) {
    main = createError ? (
      <Centered>
        <p className="text-sm text-red-600 dark:text-red-400">{createError}</p>
        <NewChatButton onClick={openNewChat} creating={creating} />
      </Centered>
    ) : (
      <Centered><Loader2 className="w-6 h-6 animate-spin text-brand-blue" /></Centered>
    );
  } else if (chat.state === 'loading') {
    main = <Centered><Loader2 className="w-6 h-6 animate-spin text-brand-blue" /></Centered>;
  } else if (chat.state === 'missing' || chat.state === 'error') {
    main = (
      <Centered>
        <p className="text-[15px] font-semibold text-brand-dark">
          {chat.state === 'missing' ? 'This chat no longer exists.' : 'Couldn’t load this chat.'}
        </p>
        <NewChatButton onClick={openNewChat} creating={creating} />
      </Centered>
    );
  } else {
    main = (
      <ChatInterface
        chat={chat}
        wide
        className="bg-surface"
        placeholder="Ask about your meetings…"
        markdownComponents={askMarkdownComponents}
        markdownClassName="chat-markdown"
        emptyState={<EmptyChat onPrompt={chat.sendMessage} disabled={chat.loading} />}
      />
    );
  }

  return (
    <Layout>
      {/* No card. The bordered, rounded box with its own scrollbar is gone, and
          so is the h-[calc(100dvh-13rem)] guess that produced it - 13rem was
          never the real chrome height, so the pane was short on some phones and
          clipped the starter prompts on others.

          The negative margins cancel the shell's page gutter exactly, so the
          chat is full-bleed, and the heights are the real arithmetic: the
          viewport less the phone top bar and bottom nav, or the whole viewport
          from the tablet up. */}
      <div
        className="-mx-4 -mb-[calc(var(--mobile-nav-h)+1rem)] -mt-4 flex h-[calc(100dvh-3.5rem-var(--mobile-nav-h))] bg-surface tablet:-mx-6 tablet:-mb-8 tablet:-mt-6 tablet:h-dvh lg:-mx-8"
      >
        {/* The chat list as a column, on the same 1100px rule the meeting page
            uses for its Ask AI panel. Below that it is the drawer. */}
        <aside className="hidden w-66 flex-none flex-col border-r border-line bg-sidebar wide:flex">{list}</aside>

        <section className="flex min-w-0 flex-1 flex-col">
          {header}
          <div className="flex min-h-0 flex-1 flex-col">{main}</div>
        </section>
      </div>

      {/* Mobile: the chat list as a drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-60 flex wide:hidden">
          <div
            aria-hidden="true"
            className="absolute inset-0 bg-brand-dark/40 backdrop-blur-[2px] dark:bg-black/60"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="relative flex h-full w-80 max-w-[85%] flex-col bg-sidebar shadow-xl">
            <div className="flex-none flex items-center justify-between px-4 pt-4">
              <span className="text-[15px] font-extrabold text-brand-dark">Chats</span>
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                className="p-1.5 rounded-lg text-muted hover:text-brand-dark"
                aria-label="Close chats"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 min-h-0">{list}</div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title={deleteTarget === 'all' ? 'Delete all chats?' : 'Delete chat?'}
        busy={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      >
        {deleteTarget === 'all' ? (
          <>Delete every Ask AI chat and its messages? This can&apos;t be undone.</>
        ) : deleteTarget ? (
          <>
            Delete <span className="font-semibold text-brand-dark">"{deleteTarget.title}"</span> and its messages?
            This can&apos;t be undone.
          </>
        ) : null}
      </ConfirmDialog>
    </Layout>
  );
};

const NewChatButton = ({ onClick, creating }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={creating}
    className="btn-primary flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-bold transition-opacity hover:opacity-90 disabled:opacity-50"
  >
    {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
    New chat
  </button>
);
