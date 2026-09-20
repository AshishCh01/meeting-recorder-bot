import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { PanelLeft, Search } from 'lucide-react';
import { Logo } from './Logo';
import { AccountMenu } from './shell/AccountMenu';
import { CommandPalette } from './shell/CommandPalette';
import { NAV, NAV_ITEMS, isCurrentPath } from './shell/nav';
import { useSidebar } from '../context/SidebarContext';

/**
 * The app shell: sidebar, phone top bar, phone bottom nav.
 *
 * Three layouts, not two. The old shell had a single `lg:` switch, so a
 * tablet got the phone's bottom bar stretched across 900px:
 *
 *   < 720px          phone   top bar + bottom nav, no sidebar
 *   720px - 1023px   tablet  icon rail, no bottom nav
 *   >= 1024px        desktop full sidebar, collapsible to the same rail
 *
 * Every margin below is a min-width variant (`tablet:`, `lg:`), which is what
 * keeps a rail preference saved on a desktop from leaving a 72px gap on a
 * phone where the sidebar is not rendered at all. Write these as max-width
 * rules and that bug comes straight back.
 */
export const Layout = ({ children, wide = false }) => {
  const location = useLocation();
  const { isRail, toggleRail } = useSidebar();
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      } else if (e.key === 'Escape') {
        setPaletteOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // Two states per element: the rail form, and the rail form that expands at
  // lg. Both branches are complete literal class strings because Tailwind
  // scans the source as text - an interpolated fragment never reaches the
  // stylesheet.
  const asideW = isRail ? 'w-18 lg:w-18' : 'w-18 lg:w-66';
  const mainML = isRail ? 'tablet:ml-18 lg:ml-18' : 'tablet:ml-18 lg:ml-66';
  const topRow = isRail
    ? 'flex-col gap-1.5 px-0 py-3'
    : 'flex-col gap-1.5 px-0 py-3 lg:h-15 lg:flex-row lg:gap-2.5 lg:px-4 lg:py-0';
  const rowCls = isRail ? 'justify-center px-0' : 'justify-center px-0 lg:justify-start lg:px-2.5';
  const labelCls = isRail ? 'hidden' : 'hidden lg:inline';

  return (
    <div className="min-h-screen bg-page">
      <aside
        className={`fixed inset-y-0 left-0 z-40 hidden flex-col border-r border-line bg-sidebar transition-[width] duration-200 tablet:flex ${asideW}`}
      >
        <div className={`flex flex-none items-center ${topRow}`}>
          <Link to="/dashboard" className="flex min-w-0 items-center gap-2.5" aria-label="MeetIQ">
            <Logo className="h-7 w-7 shrink-0" />
            <span className={`text-base font-extrabold tracking-tight text-brand-dark ${labelCls}`}>
              MeetIQ
            </span>
          </Link>
          {/* Never hidden in the rail - it is the only way back out of it. */}
          <button
            type="button"
            onClick={toggleRail}
            aria-label={isRail ? 'Expand sidebar' : 'Collapse sidebar'}
            title={isRail ? 'Expand sidebar' : 'Collapse sidebar'}
            className={`hidden h-9 w-9 items-center justify-center rounded-[10px] text-muted transition-colors hover:bg-tint-2 hover:text-brand-dark lg:inline-flex ${
              isRail ? '' : 'lg:ml-auto'
            }`}
          >
            <PanelLeft className={`h-4.5 w-4.5 ${isRail ? 'rotate-180' : ''}`} />
          </button>
        </div>

        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          aria-label="Search meetings and pages"
          className={`mx-3 mb-2.5 flex h-9 flex-none items-center gap-2.5 rounded-[10px] border border-line bg-surface text-[13.5px] text-muted transition-colors hover:border-border ${rowCls}`}
        >
          <Search className="h-4 w-4 shrink-0" />
          <span className={labelCls}>Search</span>
          <kbd
            className={`ml-auto rounded-[5px] border border-line px-1.5 py-px font-mono text-[10.5px] ${labelCls}`}
          >
            ⌘K
          </kbd>
        </button>

        {/* overflow-x-hidden is deliberate: overflow-y:auto makes the X axis
            scrollable too, which put a stray horizontal scrollbar above the
            account row in the rail. */}
        <nav className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 pb-3 pt-0.5">
          {NAV.map((group) => (
            <div key={group.label} className="mt-4.5 first:mt-0">
              <span
                className={`px-2.5 pb-1.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted ${
                  isRail ? 'hidden' : 'hidden lg:block'
                }`}
              >
                {group.label}
              </span>
              {group.items.map((item) => {
                const active = isCurrentPath(location.pathname, item.href);
                const Icon = item.icon;
                return (
                  <Link
                    key={item.id}
                    to={item.href}
                    title={item.label}
                    aria-label={item.label}
                    aria-current={active ? 'page' : undefined}
                    className={`relative mt-0.5 flex h-9.5 items-center gap-2.5 rounded-[9px] text-sm transition-colors ${rowCls} ${
                      active
                        ? "bg-tint-2 font-bold text-brand-dark before:absolute before:-left-3 before:top-2 before:bottom-2 before:w-[3px] before:rounded-r-[3px] before:bg-brand-blue before:content-['']"
                        : 'font-semibold text-body hover:bg-tint-2 hover:text-brand-dark'
                    }`}
                  >
                    <Icon className={`h-4.5 w-4.5 shrink-0 ${active ? 'text-brand-blue' : 'text-muted'}`} />
                    <span className={labelCls}>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="flex-none border-t border-line px-3 py-2.5 pb-[calc(0.625rem+env(safe-area-inset-bottom,0px))]">
          <AccountMenu labelCls={labelCls} rowCls={rowCls} align="up" />
        </div>
      </aside>

      {/* Phone top bar. The wordmark survives here even though the sidebar's
          is hidden in the rail - this is the only branding on the screen. */}
      <header className="fixed inset-x-0 top-0 z-40 flex h-14 items-center gap-2.5 border-b border-line bg-surface/85 px-3.5 backdrop-blur-lg tablet:hidden">
        <Link to="/dashboard" className="flex items-center gap-2.5" aria-label="MeetIQ">
          <Logo className="h-7 w-7" />
          <span className="text-base font-extrabold tracking-tight text-brand-dark">MeetIQ</span>
        </Link>
        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          aria-label="Search"
          className="ml-auto inline-flex h-9.5 w-9.5 items-center justify-center rounded-[10px] text-muted transition-colors hover:bg-tint-2 hover:text-brand-dark"
        >
          <Search className="h-4.5 w-4.5" />
        </button>
        <AccountMenu align="down" />
      </header>

      <nav
        className="fixed inset-x-0 bottom-0 z-40 grid h-(--mobile-nav-h) grid-cols-4 border-t border-line bg-surface/85 pb-[env(safe-area-inset-bottom,0px)] backdrop-blur-lg tablet:hidden"
        aria-label="Main"
      >
        {NAV_ITEMS.map((item) => {
          const active = isCurrentPath(location.pathname, item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.id}
              to={item.href}
              aria-current={active ? 'page' : undefined}
              className={`flex flex-col items-center justify-center gap-0.5 text-[10.5px] font-bold transition-colors ${
                active ? 'text-brand-blue' : 'text-muted'
              }`}
            >
              <Icon className="h-5 w-5" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className={`flex min-h-screen flex-col transition-[margin] duration-200 ${mainML}`}>
        {/* `wide` opts a page out of the reading-width cap: the meeting page
            manages its own full-bleed columns and must not be centred inside a
            narrower box. Everything else stops at 1100px so rows do not stretch
            across a 1920px monitor. */}
        <main
          className={`w-full flex-1 px-4 pb-[calc(var(--mobile-nav-h)+1rem)] pt-[calc(3.5rem+1rem)] tablet:px-6 tablet:pb-8 tablet:pt-6 lg:px-8 ${
            wide ? '' : 'mx-auto max-w-[1100px]'
          }`}
        >
          {children}
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
};
