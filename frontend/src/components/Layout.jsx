import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { Link, useLocation } from 'react-router-dom';
import { LayoutDashboard, LogOut, PanelLeftClose, PanelLeftOpen, CalendarClock, Sparkles, Settings } from 'lucide-react';
import meetiqLogo from '../assets/MeetIQ.png.png';

export const Layout = ({ children }) => {
  const { user, signOut } = useAuth();
  const location = useLocation();
  const [isCollapsed, setIsCollapsed] = useState(false);

  const navigation = [
    { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
    { name: 'Settings', href: '/settings', icon: Settings },
  ];

  // "Meetings" and "Settings" have real pages - "Upcoming"/"Ask AI" are
  // shown as muted placeholders (not Links) so the mobile nav matches the
  // design reference without linking anywhere that 404s.
  const mobileNavItems = [
    { name: 'Meetings', href: '/dashboard', icon: LayoutDashboard, active: true },
    { name: 'Upcoming', icon: CalendarClock, active: false },
    { name: 'Ask AI', icon: Sparkles, active: false },
    { name: 'Settings', href: '/settings', icon: Settings, active: true },
  ];

  return (
    <div className="min-h-screen bg-slate-50 flex">
      {/* Sidebar - Desktop */}
      <div
        className={`hidden md:flex flex-col fixed inset-y-0 bg-sidebar border-r border-line z-50 transition-all duration-300 ${
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
              className="text-faint hover:text-body transition-colors p-1 rounded hover:bg-white"
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
                      ? 'bg-brand-blue/10 text-brand-blue'
                      : 'text-body hover:bg-white hover:text-brand-dark'
                  }`}
                >
                  <item.icon
                    className={`${isCollapsed ? 'h-6 w-6' : 'mr-3 h-5 w-5'} shrink-0 ${
                      isActive ? 'text-brand-blue' : 'text-faint group-hover:text-muted'
                    }`}
                  />
                  {!isCollapsed && <span>{item.name}</span>}
                </Link>
              );
            })}
          </nav>
        </div>
        
        <div className="shrink-0 flex flex-col p-4 border-t border-line">
          <div className={`flex items-center ${isCollapsed ? 'justify-center mb-4' : 'px-3 py-2 mb-2'}`}>
            <div className="w-8 h-8 rounded-full bg-border-strong flex items-center justify-center text-body font-medium shrink-0" title={user?.email}>
              {user?.email?.charAt(0).toUpperCase()}
            </div>
            {!isCollapsed && (
              <div className="ml-3 truncate">
                <p className="text-sm font-medium text-brand-dark truncate">{user?.email}</p>
              </div>
            )}
          </div>
          <button
            onClick={signOut}
            title={isCollapsed ? "Sign out" : undefined}
            className={`flex items-center w-full ${isCollapsed ? 'justify-center px-0 py-3' : 'px-3 py-3'} text-sm font-medium text-body rounded-xl hover:bg-white hover:text-brand-dark transition-colors`}
          >
            <LogOut className={`${isCollapsed ? 'h-5 w-5' : 'mr-3 h-5 w-5'} text-faint shrink-0`} />
            {!isCollapsed && <span>Sign out</span>}
          </button>
        </div>
      </div>

      {/* Mobile top bar (simplified) */}
      <div className="md:hidden fixed top-0 w-full h-16 bg-white border-b border-line z-50 flex items-center justify-between px-4">
        <div className="flex items-center space-x-2">
          <img src={meetiqLogo} alt="MeetIQ" className="h-6 w-6 object-contain" />
          <span className="text-lg font-bold text-brand-dark tracking-tight">Meet<span className="text-brand-blue">IQ</span></span>
        </div>
        <button onClick={signOut} className="text-muted p-2">
          <LogOut className="h-5 w-5" />
        </button>
      </div>

      {/* Mobile bottom nav */}
      <div className="md:hidden fixed bottom-0 w-full bg-white border-t border-line z-50 grid grid-cols-4 px-2 pt-2 pb-[env(safe-area-inset-bottom,0.5rem)]">
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
      <div className={`flex-1 transition-all duration-300 ${isCollapsed ? 'md:ml-20' : 'md:ml-64'} pt-16 md:pt-0 pb-20 md:pb-0 min-h-screen`}>
        <main className="w-full px-4 sm:px-6 md:px-8 py-8">
          {children}
        </main>
      </div>
    </div>
  );
};
