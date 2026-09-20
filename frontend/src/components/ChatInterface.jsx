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
      <div className="flex-1 overflow-y-auto p-4.5">
        <div className={`flex flex-col gap-3.5 min-h-full ${column}`}>
          {messages.length === 0 && emptyState}

          {messages.map((msg) => {
            const isUser = msg.role === 'user';
            return (
              <div key={msg.id} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`max-w-[88%] px-4 py-3 rounded-2xl text-[14.5px] leading-relaxed border ${
                    isUser
                      ? 'bg-brand-blue text-white border-brand-blue'
                      : 'bg-surface text-brand-dark border-border-strong'
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
              <div className="bg-surface border border-border-strong rounded-2xl px-4 py-3.5 flex items-center gap-2.5">
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
      <div className="flex-none px-4.5 py-3.5 border-t border-line">
        <div className={`flex flex-col gap-2.5 ${column}`}>
          {suggestions.length > 0 && (
            <div className="flex gap-1.5 flex-wrap">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  disabled={loading || disabled}
                  className="px-2.5 py-1.5 rounded-full border border-border bg-surface text-xs font-semibold text-body hover:border-brand-blue/40 disabled:opacity-50 transition-colors"
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
              className="flex-1 h-11 px-3.5 border border-border rounded-xl bg-surface text-sm text-brand-dark placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-blue/20 focus:border-brand-blue transition-all disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!input.trim() || loading || disabled}
              className="flex-none w-11 h-11 rounded-xl bg-linear-to-br from-brand-blue to-brand-blue-light text-white flex items-center justify-center disabled:opacity-50 transition-opacity"
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
