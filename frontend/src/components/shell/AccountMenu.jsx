import React, { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ChevronDown, LogOut, Settings as SettingsIcon, Sun, Moon } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useTheme } from '../../context/ThemeContext';

// The email's local part, used as the identity line. The full address is a
// poor label - it truncates to "ashishchoudhary5430@gmai…" in a 264px sidebar
// and tells the user something they already know. It lives in the menu
// instead, where there is room for all of it.
const displayName = (email) => {
  if (!email) return 'Account';
  const local = email.split('@')[0].replace(/[._-]+/g, ' ').trim();
  return local.charAt(0).toUpperCase() + local.slice(1);
};

/**
 * One account button that opens a menu, replacing the three stacked rows
 * (identity, theme, sign out) the sidebar footer used to carry.
 *
 * The caller passes the same `labelCls` / `rowCls` strings the rest of the
 * sidebar uses, rather than a `compact` boolean. Compactness is a breakpoint
 * question as much as a rail question - the tablet is always a rail whatever
 * the saved desktop preference says - and a boolean can only see the
 * preference, which left the chevron hanging off a 72px rail.
 *
 * `align` decides which way the menu opens: up from the sidebar footer, down
 * from the top bar.
 */
export const AccountMenu = ({ labelCls = 'hidden', rowCls = 'justify-center', align = 'up' }) => {
  const { user, signOut } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  // Navigating with the menu open would otherwise leave it hanging over the
  // new page.
  useEffect(() => setOpen(false), [location.pathname]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const handleSignOut = async () => {
    await signOut();
    navigate('/', { replace: true });
  };

  const isDark = theme === 'dark';
  const initial = user?.email?.charAt(0).toUpperCase() || '?';

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        title={user?.email}
        className={`flex w-full items-center gap-2.5 rounded-[10px] border border-transparent py-[7px] text-left transition-colors hover:bg-tint-2 ${rowCls}`}
      >
        <span className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-[9px] bg-linear-to-br from-brand-blue to-brand-blue-deep text-[12.5px] font-extrabold text-white">
          {initial}
        </span>
        <span className={`min-w-0 flex-1 truncate text-[13.5px] font-bold text-brand-dark ${labelCls}`}>
          {displayName(user?.email)}
        </span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-muted ${labelCls}`} />
      </button>

      {open && (
        <div
          role="menu"
          className={`absolute z-60 w-60 overflow-hidden rounded-xl border border-border bg-surface py-1.5 shadow-xl ${
            align === 'up' ? 'bottom-full mb-2 left-0' : 'top-full mt-2 right-0'
          }`}
        >
          <div className="truncate px-3 py-2 text-xs text-muted" title={user?.email}>
            {user?.email}
          </div>
          <hr className="my-1 border-line" />
          <Link
            to="/settings"
            role="menuitem"
            className="flex items-center gap-2.5 px-3 py-2 text-sm text-body transition-colors hover:bg-surface-hover hover:text-brand-dark"
          >
            <SettingsIcon className="h-4 w-4 text-muted" />
            Settings
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={toggleTheme}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-sm text-body transition-colors hover:bg-surface-hover hover:text-brand-dark"
          >
            {isDark ? <Sun className="h-4 w-4 text-muted" /> : <Moon className="h-4 w-4 text-muted" />}
            {isDark ? 'Light mode' : 'Dark mode'}
          </button>
          <hr className="my-1 border-line" />
          <button
            type="button"
            role="menuitem"
            onClick={handleSignOut}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-sm text-red-600 transition-colors hover:bg-surface-hover dark:text-red-400"
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
};
