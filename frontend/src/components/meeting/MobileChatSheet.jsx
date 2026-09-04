import React, { useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { X, ChevronUp, Send, Loader2 } from 'lucide-react';
import { useMeetingChat } from '../../hooks/useMeetingChat';

const SUGGESTIONS = ['What did I commit to?', 'What was decided?'];
const DRAG_THRESHOLD = 60;

export const MobileChatSheet = ({ meetingId, meetingTitle }) => {
  const [expanded, setExpanded] = useState(false);
  const { messages, input, setInput, loading, streaming, status, sendMessage, messagesEndRef } = useMeetingChat(meetingId);
  const dragStartY = useRef(null);

  const handleTouchStart = (e) => {
    dragStartY.current = e.touches[0].clientY;
  };

  const handleTouchEnd = (e) => {
    if (dragStartY.current === null) return;
    const delta = dragStartY.current - e.changedTouches[0].clientY;
    if (delta > DRAG_THRESHOLD) setExpanded(true);
    else if (delta < -DRAG_THRESHOLD) setExpanded(false);
    dragStartY.current = null;
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    sendMessage();
  };

  const handleSuggestionClick = (text) => {
    setExpanded(true);
    sendMessage(text);
  };

  return (
    <div
      className={`lg:hidden fixed left-0 right-0 bg-surface border-t border-border-strong shadow-[0_-10px_30px_rgba(15,23,32,0.12)] flex flex-col transition-[top,bottom] duration-300 ${
        expanded ? 'inset-0 rounded-none z-60' : 'bottom-17 rounded-t-[20px] z-40'
      }`}
    >
      {expanded ? (
        <>
          <div className="flex-none flex items-center gap-2.5 px-4 py-3 bg-status-muted-bg">
            <button onClick={() => setExpanded(false)} className="text-brand-blue">
              <X className="w-5 h-5" />
            </button>
            <div className="min-w-0">
              <div className="text-sm font-extrabold text-brand-dark">Ask about this meeting</div>
              <div className="text-xs text-muted truncate">{meetingTitle}</div>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-3">
            {messages.map((msg, idx) => {
              const isUser = msg.role === 'user';
              return (
                <div key={idx} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                  <div
                    className={`max-w-[86%] px-3.5 py-3 rounded-2xl text-sm leading-relaxed border ${
                      isUser
                        ? 'bg-brand-blue text-white border-brand-blue'
                        : 'bg-surface text-brand-dark border-border-strong'
                    }`}
                  >
                    {isUser ? msg.content : <ReactMarkdown>{msg.content}</ReactMarkdown>}
                  </div>
                </div>
              );
            })}
            {loading && !streaming && (
              <div className="text-xs text-muted">{status || 'Thinking…'}</div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </>
      ) : (
        <div
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
          onClick={() => setExpanded(true)}
          className="flex-none px-4.5 pt-2.5 pb-5 flex flex-col gap-3 cursor-pointer"
        >
          <div className="self-center w-9 h-1 rounded-full bg-border-strong" />
          <div className="flex items-center gap-2.5">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-extrabold text-brand-dark">Ask about this meeting</div>
              <div className="text-xs text-muted">Drag up for full chat</div>
            </div>
            <ChevronUp className="w-4 h-4 text-faint" />
          </div>
          <div className="flex gap-1.5 overflow-x-auto">
            {SUGGESTIONS.map((s) => (
              <span
                key={s}
                onClick={(e) => {
                  e.stopPropagation();
                  handleSuggestionClick(s);
                }}
                className="flex-none px-2.5 py-1.5 rounded-full border border-border text-xs font-semibold text-body whitespace-nowrap"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        onClick={(e) => {
          if (!expanded) {
            e.preventDefault();
            setExpanded(true);
          }
        }}
        className="flex-none px-4.5 pb-5 pt-1 flex gap-2.5"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onFocus={() => setExpanded(true)}
          placeholder="Ask anything…"
          className="flex-1 h-11 px-3.5 border border-border rounded-xl text-sm text-brand-dark placeholder:text-faint focus:outline-none focus:border-brand-blue"
        />
        <button
          type="submit"
          disabled={!input.trim() || loading}
          className="flex-none w-11 h-11 rounded-xl bg-linear-to-br from-brand-blue to-brand-blue-light text-white flex items-center justify-center disabled:opacity-50"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
        </button>
      </form>
    </div>
  );
};
