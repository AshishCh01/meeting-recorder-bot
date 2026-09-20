import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, X } from 'lucide-react';

const ToastContext = createContext({});

const DISMISS_AFTER = 6000;

/**
 * Replaces window.alert() for the app's failure paths.
 *
 * alert() blocks the whole tab, looks like a browser error rather than part of
 * the product, and cannot say anything after the user dismisses it. It was the
 * single fastest way to make the app read as unfinished.
 *
 * Errors stay until dismissed or replaced; successes time out. Nothing here
 * announces itself over the top of what the user is doing, so the toast is an
 * aside rather than an interruption.
 */
export const ToastProvider = ({ children }) => {
  const [toasts, setToasts] = useState([]);
  const seq = useRef(0);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (message, tone = 'error') => {
      if (!message) return undefined;
      const id = ++seq.current;
      setToasts((list) => [...list, { id, message, tone }]);
      // Errors persist: a failure the user missed is a failure they will hit
      // again. Confirmations are safe to let go.
      if (tone !== 'error') {
        timers.current.set(id, setTimeout(() => dismiss(id), DISMISS_AFTER));
      }
      return id;
    },
    [dismiss]
  );

  const value = useMemo(
    () => ({
      toast: (message) => push(message, 'error'),
      toastSuccess: (message) => push(message, 'success'),
    }),
    [push]
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* aria-live so a screen reader hears the failure it can no longer be
          interrupted by. Errors are assertive, confirmations polite. */}
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-90 flex flex-col items-center gap-2 p-4 tablet:items-end tablet:p-6">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.tone === 'error' ? 'alert' : 'status'}
            aria-live={t.tone === 'error' ? 'assertive' : 'polite'}
            className={`pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-xl border p-3.5 shadow-xl ${
              t.tone === 'error'
                ? 'border-status-failed-fg/25 bg-status-failed-bg'
                : 'border-accent-line bg-accent-soft'
            }`}
          >
            {t.tone === 'error' ? (
              <AlertCircle className="mt-px h-4.5 w-4.5 flex-none text-status-failed-fg" />
            ) : (
              <CheckCircle2 className="mt-px h-4.5 w-4.5 flex-none text-accent-ink" />
            )}
            <p
              className={`min-w-0 flex-1 break-words text-[13.5px] leading-relaxed ${
                t.tone === 'error' ? 'text-status-failed-fg' : 'text-accent-ink'
              }`}
            >
              {t.message}
            </p>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss"
              className={`-m-1 flex-none rounded-md p-1 opacity-70 transition-opacity hover:opacity-100 ${
                t.tone === 'error' ? 'text-status-failed-fg' : 'text-accent-ink'
              }`}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
};

export const useToast = () => useContext(ToastContext);
