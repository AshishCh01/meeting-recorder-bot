import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, CornerDownLeft } from 'lucide-react';
import api from '../../lib/api';
import { NAV_ITEMS } from './nav';

/**
 * ⌘K / Ctrl+K search over the app's pages and the user's meetings.
 *
 * Meetings are fetched the first time the palette opens, not on mount - the
 * shell wraps every page, and a list request on every navigation would be
 * three wasted round trips before the user has pressed anything. The result is
 * cached for the session; a meeting recorded after that shows up on reload,
 * which is an acceptable trade for not polling here.
 */
export const CommandPalette = ({ open, onClose }) => {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [meetings, setMeetings] = useState([]);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef(null);
  const loadedRef = useRef(false);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setCursor(0);
    inputRef.current?.focus();
    if (loadedRef.current) return;
    loadedRef.current = true;
    api
      .get('/meetings')
      .then(({ data }) => setMeetings(Array.isArray(data) ? data.slice(0, 20) : []))
      .catch(() => {
        // A palette that can still jump between pages is better than no
        // palette; let the meetings section stay empty.
        loadedRef.current = false;
      });
  }, [open]);

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const match = (label) => label.toLowerCase().includes(needle);
    const out = [];

    const pages = NAV_ITEMS.filter((i) => match(i.label)).map((i) => ({
      key: `page-${i.id}`,
      label: i.label,
      icon: i.icon,
      href: i.href,
    }));
    if (pages.length) out.push({ section: 'Go to', items: pages });

    const hits = meetings
      .filter((m) => match(m.title || m.meeting_url || ''))
      .slice(0, 6)
      .map((m) => ({
        key: `meeting-${m.id}`,
        label: m.title || m.meeting_url,
        icon: NAV_ITEMS[0].icon,
        href: `/meetings/${m.id}`,
      }));
    if (hits.length) out.push({ section: 'Meetings', items: hits });

    return out;
  }, [query, meetings]);

  // One flat list behind the grouped display, so ↑/↓ crosses section borders
  // the way every other palette does.
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  const go = useCallback(
    (href) => {
      onClose();
      navigate(href);
    },
    [navigate, onClose]
  );

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setCursor((c) => (flat.length ? (c + 1) % flat.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setCursor((c) => (flat.length ? (c - 1 + flat.length) % flat.length : 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const hit = flat[cursor];
      if (hit) go(hit.href);
    }
  };

  if (!open) return null;

  let index = -1;

  return (
    <div className="fixed inset-0 z-100" role="dialog" aria-modal="true" aria-label="Search">
      <button
        type="button"
        aria-label="Close search"
        onClick={onClose}
        className="absolute inset-0 w-full bg-brand-dark/40 backdrop-blur-[2px] dark:bg-black/60"
      />
      <div className="relative mx-auto mt-[12vh] w-[calc(100%-2rem)] max-w-xl overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center gap-2.5 border-b border-line px-4">
          <Search className="h-4 w-4 shrink-0 text-muted" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setCursor(0);
            }}
            onKeyDown={onKeyDown}
            placeholder="Search meetings and pages…"
            aria-label="Search meetings and pages"
            className="h-13 flex-1 bg-transparent text-base text-brand-dark placeholder:text-faint focus:outline-none"
          />
          <kbd className="hidden shrink-0 rounded-md border border-line px-1.5 py-0.5 font-mono text-[10.5px] text-muted sm:block">
            esc
          </kbd>
        </div>

        <div className="max-h-[52vh] overflow-y-auto overflow-x-hidden p-2">
          {flat.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-muted">No matches.</p>
          ) : (
            groups.map((group) => (
              <div key={group.section} className="mb-1 last:mb-0">
                <div className="px-3 pb-1 pt-2 font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted">
                  {group.section}
                </div>
                {group.items.map((item) => {
                  index += 1;
                  const active = index === cursor;
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onMouseEnter={() => setCursor(flat.indexOf(item))}
                      onClick={() => go(item.href)}
                      className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition-colors ${
                        active ? 'bg-accent-soft text-brand-dark' : 'text-body'
                      }`}
                    >
                      <Icon className={`h-4 w-4 shrink-0 ${active ? 'text-brand-blue' : 'text-muted'}`} />
                      <span className="min-w-0 flex-1 truncate">{item.label}</span>
                      {active && <CornerDownLeft className="h-3.5 w-3.5 shrink-0 text-muted" />}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
