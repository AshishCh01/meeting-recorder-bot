import React, { useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import { X, Send, Loader2 } from 'lucide-react';
import { useMeetingChatContext } from '../../context/MeetingChatContext';

const SUGGESTIONS = ['What did I commit to?', 'What was decided?'];

/**
 * The chat surface below 1100px, where the side column would squeeze the
 * content column rather than sit beside it.
 *
 * It used to be permanently docked over the bottom of the page - a peek card
 * plus its own composer - which on a 375px screen took a fixed slice of a
 * viewport the summary was already short of. Now it is a sheet that opens from
 * the header button and covers the screen while it is open, so the content
 * gets the whole viewport the rest of the time.
 */
export const MobileChatSheet = ({ meetingTitle, open, onClose }) => {
  const { messages, input, setInput, loading, streaming, status, sendMessage, messagesEndRef } =
    useMeetingChatContext();

  // The sheet scrolls its own message list; letting the page behind it scroll
  // too is what makes a full-screen sheet feel broken on a phone.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    sendMessage();
  };

  return (
    <div className="fixed inset-0 z-70 flex flex-col bg-surface wide:hidden" role="dialog" aria-modal="true" aria-label="Ask about this meeting">
      <div className="flex flex-none items-center gap-2.5 border-b border-line px-4 py-3">
        <button type="button" onClick={onClose} aria-label="Close chat" className="text-muted transition-colors hover:text-brand-dark">
          <X className="h-5 w-5" />
        </button>
        <div className="min-w-0">
          <div className="text-sm font-extrabold text-brand-dark">Ask about this meeting</div>
          <div className="truncate text-xs text-muted">{meetingTitle}</div>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-3 overflow-y-auto overflow-x-hidden px-4 py-4">
        {messages.length === 0 && (
          <div className="flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => sendMessage(s)}
                className="rounded-full border border-border px-2.5 py-1.5 text-xs font-semibold text-body transition-colors hover:border-brand-blue hover:text-brand-dark"
              >
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((msg) => {
          const isUser = msg.role === 'user';
          return (
            <div key={msg.id} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[86%] break-words rounded-2xl border px-3.5 py-3 text-sm leading-relaxed ${
                  isUser ? 'btn-primary border-transparent' : 'chat-markdown border-line bg-surface-hover text-brand-dark'
                }`}
              >
                {isUser ? msg.content : <ReactMarkdown>{msg.content}</ReactMarkdown>}
              </div>
            </div>
          );
        })}
        {loading && !streaming && <div className="text-xs text-muted">{status || 'Thinking…'}</div>}
        <div ref={messagesEndRef} />
      </div>

      <form
        onSubmit={handleSubmit}
        className="flex flex-none gap-2.5 border-t border-line px-4 pb-[calc(0.75rem+env(safe-area-inset-bottom,0px))] pt-3"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask anything…"
          /* 16px: below that, iOS Safari zooms the page on focus. */
          className="h-11 flex-1 rounded-xl border border-border bg-surface px-3.5 text-base text-brand-dark placeholder:text-muted focus:border-brand-blue focus:outline-none"
        />
        <button
          type="submit"
          disabled={!input.trim() || loading}
          aria-label="Send"
          className="btn-primary flex h-11 w-11 flex-none items-center justify-center rounded-xl disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </button>
      </form>
    </div>
  );
};
