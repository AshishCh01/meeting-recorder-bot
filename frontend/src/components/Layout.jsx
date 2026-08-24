import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { LayoutDashboard, LogOut, PanelLeftClose, PanelLeftOpen, CalendarClock, Sparkles, Settings } from 'lucide-react';
import { ThemeToggle } from './ThemeToggle';
import meetiqLogo from '../assets/logo.png';

export const Layout = ({ children }) => {
  const { user, signOut } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [isCollapsed, setIsCollapsed] = useState(false);

  const handleSignOut = async () => {
    await signOut();
    navigate('/', { replace: true });
  };

  const navigation = [
    { name: 'Meetings', href: '/dashboard', icon: LayoutDashboard },
    { name: 'Upcoming', href: '/upcoming', icon: CalendarClock },
    { name: 'Settings', href: '/settings', icon: Settings },
  ];

  // "Meetings", "Upcoming" and "Settings" have real pages - "Ask AI" is
  // still shown as a muted placeholder (not a Link) so the mobile nav
  // matches the design reference without linking anywhere that 404s.
  const mobileNavItems = [
    { name: 'Meetings', href: '/dashboard', icon: LayoutDashboard, active: true },
    { name: 'Upcoming', href: '/upcoming', icon: CalendarClock, active: true },
    { name: 'Ask AI', icon: Sparkles, active: false },
    { name: 'Settings', href: '/settings', icon: Settings, active: true },
  ];

  return (
    <div className="min-h-screen bg-page flex">
      {/* Sidebar - Desktop */}
      <div
        className={`hidden lg:flex flex-col fixed inset-y-0 bg-sidebar border-r border-line z-50 transition-all duration-300 ${
          isCollapsed ? 'w-20' : 'w-64'
        }`}
      >
        <div className="flex-1 flex flex-col pt-8 pb-4 overflow-y-auto">
          <div className={`flex items-center mb-8 ${isCollapsed ? 'flex-col gap-4 px-2' : 'px-6 justify-between'}`}>
            <div className="flex items-center gap-2" title="MeetIQ">
              <img src={meetiqLogo} alt="MeetIQ" className="h-8 w-8 object-contain shrink-0" />
              {!isCollapsed && (
                <span className="text-xl font-bold text-brand-dark tracking-tight">
                  Meet<span className="text-brand-blue">IQ</span>
                </span>
              )}
            </div>
            <button
              onClick={() => setIsCollapsed(!isCollapsed)}
              className="text-faint hover:text-body transition-colors p-1 rounded hover:bg-surface-hover"
              title={isCollapsed ? "Expand Sidebar" : "Collapse Sidebar"}
            >
              {isCollapsed ? <PanelLeftOpen className="h-5 w-5" /> : <PanelLeftClose className="h-5 w-5" />}
            </button>
          </div>

          <nav className="mt-5 flex-1 px-4 space-y-1">
            {navigation.map((item) => {
              const isActive = location.pathname === item.href;
              return (
                <Link
                  key={item.name}
                  to={item.href}
                  title={isCollapsed ? item.name : undefined}
                  className={`group flex items-center ${isCollapsed ? 'justify-center px-0 py-3' : 'px-3 py-3'} text-sm font-medium rounded-xl transition-colors ${
                    isActive
                      ? 'bg-status-done-bg text-status-done-fg'
                      : 'text-body hover:bg-surface-hover hover:text-brand-dark'
                  }`}
                >
                  <item.icon
                    className={`${isCollapsed ? 'h-6 w-6' : 'mr-3 h-5 w-5'} shrink-0 ${
                      isActive ? 'text-status-done-fg' : 'text-faint group-hover:text-muted'
                    }`}
                  />
                  {!isCollapsed && <span>{item.name}</span>}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="shrink-0 flex flex-col p-4 border-t border-line gap-1">
          <div className={`flex items-center ${isCollapsed ? 'justify-center mb-3' : 'px-3 py-2 mb-1'}`}>
            <div className="w-8 h-8 rounded-full bg-border-strong flex items-center justify-center text-body font-medium shrink-0" title={user?.email}>
              {user?.email?.charAt(0).toUpperCase()}
            </div>
            {!isCollapsed && (
              <div className="ml-3 truncate">
                <p className="text-sm font-medium text-brand-dark truncate">{user?.email}</p>
              </div>
            )}
          </div>
          <ThemeToggle
            className={`flex items-center w-full ${isCollapsed ? 'justify-center px-0 py-3' : 'px-3 py-3'} text-sm font-medium text-body rounded-xl hover:bg-surface-hover hover:text-brand-dark transition-colors`}
            iconClassName={`${isCollapsed ? 'h-5 w-5' : 'mr-3 h-5 w-5'} text-faint shrink-0`}
          />
          <button
            onClick={handleSignOut}
            title={isCollapsed ? "Sign out" : undefined}
            className={`flex items-center w-full ${isCollapsed ? 'justify-center px-0 py-3' : 'px-3 py-3'} text-sm font-medium text-body rounded-xl hover:bg-surface-hover hover:text-brand-dark transition-colors`}
          >
            <LogOut className={`${isCollapsed ? 'h-5 w-5' : 'mr-3 h-5 w-5'} text-faint shrink-0`} />
            {!isCollapsed && <span>Sign out</span>}
          </button>
        </div>
      </div>

      {/* Mobile top bar (simplified) */}
      <div className="lg:hidden fixed top-0 w-full h-16 bg-surface border-b border-line z-50 flex items-center justify-between px-4">
        <div className="flex items-center space-x-2">
          <img src={meetiqLogo} alt="MeetIQ" className="h-6 w-6 object-contain" />
          <span className="text-lg font-bold text-brand-dark tracking-tight">Meet<span className="text-brand-blue">IQ</span></span>
        </div>
        <div className="flex items-center">
          <ThemeToggle className="text-muted p-2" iconClassName="h-5 w-5" />
          <button onClick={handleSignOut} className="text-muted p-2">
            <LogOut className="h-5 w-5" />
          </button>
        </div>
      </div>

      {/* Mobile bottom nav */}
      <div className="lg:hidden fixed bottom-0 w-full bg-surface border-t border-line z-50 grid grid-cols-4 px-2 pt-2 pb-[env(safe-area-inset-bottom,0.5rem)]">
        {mobileNavItems.map((item) => {
          const isActive = item.active && location.pathname === item.href;
          const content = (
            <>
              <item.icon className={`h-5 w-5 ${isActive ? 'text-brand-blue' : 'text-faint'}`} />
              <span className={`text-[11px] font-semibold ${isActive ? 'text-brand-blue' : 'text-faint'}`}>
                {item.name}
              </span>
            </>
          );
          const className = 'flex flex-col items-center gap-1 py-1.5';
          return item.active ? (
            <Link key={item.name} to={item.href} className={className}>
              {content}
            </Link>
          ) : (
            <div key={item.name} className={`${className} cursor-default`} title="Coming soon">
              {content}
            </div>
          );
        })}
      </div>

      {/* Main Content */}
      <div className={`flex-1 transition-all duration-300 ${isCollapsed ? 'lg:ml-20' : 'lg:ml-64'} pt-16 lg:pt-0 pb-20 lg:pb-0 min-h-screen`}>
        <main className="w-full px-4 sm:px-6 lg:px-8 py-8">
          {children}
        </main>
      </div>
    </div>
  );
};
