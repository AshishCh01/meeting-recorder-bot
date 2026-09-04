import React from 'react';
import ReactMarkdown from 'react-markdown';
import { Send, Loader2 } from 'lucide-react';
import meetiqLogo from '../assets/logo.png';
import { useMeetingChatContext } from '../context/MeetingChatContext';

const SUGGESTIONS = ['Summarize the decisions', 'What are the action items?', 'Any risks mentioned?'];

export const ChatInterface = () => {
  const { messages, input, setInput, loading, streaming, status, sendMessage, messagesEndRef } = useMeetingChatContext();

  const handleSubmit = (e) => {
    e.preventDefault();
    sendMessage();
  };

  return (
    <div className="flex flex-col h-full bg-sidebar">
      {/* Header */}
      <div className="flex-none px-5 py-5 border-b border-line flex items-center gap-2.5">
        <img src={meetiqLogo} alt="" className="w-7 h-7 rounded-lg object-contain" />
        <div>
          <div className="text-[15px] font-extrabold text-brand-dark tracking-tight">Ask about this meeting</div>
          <div className="text-xs text-muted">Answers cite the transcript</div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4.5 flex flex-col gap-3.5">
        {messages.map((msg, idx) => {
          const isUser = msg.role === 'user';
          return (
            <div key={idx} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[88%] px-4 py-3 rounded-2xl text-[14.5px] leading-relaxed border ${
                  isUser
                    ? 'bg-brand-blue text-white border-brand-blue'
                    : 'bg-surface text-brand-dark border-border-strong'
                }`}
              >
                {isUser ? (
                  <p>{msg.content}</p>
                ) : (
                  <div className="prose prose-sm max-w-none">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
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

      {/* Input */}
      <div className="flex-none px-4.5 py-3.5 border-t border-line flex flex-col gap-2.5">
        <div className="flex gap-1.5 flex-wrap">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => sendMessage(s)}
              disabled={loading}
              className="px-2.5 py-1.5 rounded-full border border-border bg-surface text-xs font-semibold text-body hover:border-brand-blue/40 disabled:opacity-50 transition-colors"
            >
              {s}
            </button>
          ))}
        </div>
        <form onSubmit={handleSubmit} className="flex gap-2.5">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={loading}
            placeholder="Ask anything about this call…"
            className="flex-1 h-11 px-3.5 border border-border rounded-xl bg-surface text-sm text-brand-dark placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-blue/20 focus:border-brand-blue transition-all disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!input.trim() || loading}
            className="flex-none w-11 h-11 rounded-xl bg-linear-to-br from-brand-blue to-brand-blue-light text-white flex items-center justify-center disabled:opacity-50 transition-opacity"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </form>
      </div>
    </div>
  );
};
