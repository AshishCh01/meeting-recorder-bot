import React from 'react';
import ReactMarkdown from 'react-markdown';
import { Send, Loader2 } from 'lucide-react';
import { Logo } from './Logo';
import { useMeetingChatContext } from '../context/MeetingChatContext';

const MEETING_SUGGESTIONS = ['Summarize the decisions', 'What are the action items?', 'Any risks mentioned?'];

// The chat panel, given its state as `chat` (useMeetingChat or useAskAiChat).
//
//   header             shown above the messages (omit for none)
//   suggestions        quick-question chips above the input
//   emptyState         shown instead of the messages while there are none
//   markdownComponents ReactMarkdown `components` for the agent's answers
//   markdownClassName  class on each answer's markdown wrapper
//   disabled           blocks sending (e.g. while the chat is still loading)
//   wide               centres a reading-width column, for a full-page chat
export const ChatInterface = ({
  chat,
  header = null,
  suggestions = [],
  placeholder = 'Ask anything…',
  emptyState = null,
  markdownComponents,
  markdownClassName = 'prose prose-sm max-w-none',
  disabled = false,
  wide = false,
  className = 'bg-sidebar',
}) => {
  const { messages, input, setInput, loading, streaming, status, sendMessage, messagesEndRef } = chat;
  const column = wide ? 'w-full max-w-3xl mx-auto' : '';

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!disabled) sendMessage();
  };

  return (
    <div className={`flex flex-col h-full ${className}`}>
      {header}

      {/* Messages */}
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-4.5">
        <div className={`flex flex-col gap-3.5 min-h-full ${column}`}>
          {messages.length === 0 && emptyState}

          {messages.map((msg) => {
            const isUser = msg.role === 'user';
            return (
              <div key={msg.id} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`max-w-[88%] rounded-2xl border px-4 py-3 text-[14.5px] leading-relaxed ${
                    isUser ? 'btn-primary border-transparent font-semibold' : 'border-line bg-surface text-brand-dark'
                  }`}
                >
                  {isUser ? (
                    <p className="whitespace-pre-wrap wrap-break-word">{msg.content}</p>
                  ) : (
                    <div className={markdownClassName}>
                      <ReactMarkdown components={markdownComponents}>{msg.content}</ReactMarkdown>
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {/* Only until the answer itself starts arriving - once it's streaming,
              the growing text is the progress indicator. */}
          {loading && !streaming && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2.5 rounded-2xl border border-line bg-surface px-4 py-3.5">
                <div className="flex items-center gap-1.5">
                  <div className="w-2 h-2 bg-brand-blue/40 rounded-full animate-bounce" />
                  <div className="w-2 h-2 bg-brand-blue/60 rounded-full animate-bounce" style={{ animationDelay: '0.15s' }} />
                  <div className="w-2 h-2 bg-brand-blue rounded-full animate-bounce" style={{ animationDelay: '0.3s' }} />
                </div>
                {status && <span className="text-xs text-muted">{status}</span>}
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="flex-none border-t border-line px-4.5 pb-[calc(0.875rem+env(safe-area-inset-bottom,0px))] pt-3.5">
        <div className={`flex flex-col gap-2.5 ${column}`}>
          {suggestions.length > 0 && (
            <div className="flex gap-1.5 flex-wrap">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  disabled={loading || disabled}
                  className="rounded-full border border-line bg-surface px-2.5 py-1.5 text-xs font-semibold text-body transition-colors hover:border-brand-blue hover:text-brand-dark disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <form onSubmit={handleSubmit} className="flex gap-2.5">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading || disabled}
              maxLength={4000}
              placeholder={placeholder}
              className="h-11 flex-1 rounded-xl border border-border bg-surface px-3.5 text-base text-brand-dark placeholder:text-muted transition-colors focus:border-brand-blue focus:outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!input.trim() || loading || disabled}
              className="btn-primary flex h-11 w-11 flex-none items-center justify-center rounded-xl transition-opacity disabled:opacity-50"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};

// The meeting page's chat column: one meeting's conversation, from
// MeetingChatContext (shared with the mobile sheet).
export const MeetingChatInterface = () => {
  const chat = useMeetingChatContext();
  return (
    <ChatInterface
      chat={chat}
      suggestions={MEETING_SUGGESTIONS}
      placeholder="Ask anything about this call…"
      header={
        <div className="flex-none px-5 py-5 border-b border-line flex items-center gap-2.5">
          <Logo className="w-7 h-7" />
          <div>
            <div className="text-[15px] font-extrabold text-brand-dark tracking-tight">Ask about this meeting</div>
            <div className="text-xs text-muted">Answers cite the transcript</div>
          </div>
        </div>
      }
    />
  );
};
