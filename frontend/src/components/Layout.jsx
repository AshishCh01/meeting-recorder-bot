import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { Link, useLocation } from 'react-router-dom';
import { LayoutDashboard, LogOut, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import meetiqLogo from '../assets/MeetIQ.png.png';

export const Layout = ({ children }) => {
  const { user, signOut } = useAuth();
  const location = useLocation();
  const [isCollapsed, setIsCollapsed] = useState(false);

  const navigation = [
    { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  ];

  return (
    <div className="min-h-screen bg-slate-50 flex">
      {/* Sidebar - Desktop */}
      <div 
        className={`hidden md:flex flex-col fixed inset-y-0 bg-white border-r border-slate-200 z-50 transition-all duration-300 ${
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
              className="text-slate-400 hover:text-slate-600 transition-colors p-1 rounded hover:bg-slate-50"
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
                      : 'text-slate-600 hover:bg-slate-50 hover:text-brand-dark'
                  }`}
                >
                  <item.icon
                    className={`${isCollapsed ? 'h-6 w-6' : 'mr-3 h-5 w-5'} shrink-0 ${
                      isActive ? 'text-brand-blue' : 'text-slate-400 group-hover:text-slate-500'
                    }`}
                  />
                  {!isCollapsed && <span>{item.name}</span>}
                </Link>
              );
            })}
          </nav>
        </div>
        
        <div className="flex-shrink-0 flex flex-col p-4 border-t border-slate-100">
          <div className={`flex items-center ${isCollapsed ? 'justify-center mb-4' : 'px-3 py-2 mb-2'}`}>
            <div className="w-8 h-8 rounded-full bg-slate-200 flex items-center justify-center text-slate-500 font-medium shrink-0" title={user?.email}>
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
            className={`flex items-center w-full ${isCollapsed ? 'justify-center px-0 py-3' : 'px-3 py-3'} text-sm font-medium text-slate-600 rounded-xl hover:bg-slate-50 hover:text-brand-dark transition-colors`}
          >
            <LogOut className={`${isCollapsed ? 'h-5 w-5' : 'mr-3 h-5 w-5'} text-slate-400 shrink-0`} />
            {!isCollapsed && <span>Sign out</span>}
          </button>
        </div>
      </div>

      {/* Mobile top bar (simplified) */}
      <div className="md:hidden fixed top-0 w-full h-16 bg-white border-b border-slate-200 z-50 flex items-center justify-between px-4">
        <div className="flex items-center space-x-2">
          <img src={meetiqLogo} alt="MeetIQ" className="h-6 w-6 object-contain" />
          <span className="text-lg font-bold text-brand-dark tracking-tight">Meet<span className="text-brand-blue">IQ</span></span>
        </div>
        <button onClick={signOut} className="text-slate-500 p-2">
          <LogOut className="h-5 w-5" />
        </button>
      </div>

      {/* Main Content */}
      <div className={`flex-1 transition-all duration-300 ${isCollapsed ? 'md:ml-20' : 'md:ml-64'} pt-16 md:pt-0 min-h-screen`}>
        <main className="w-full px-4 sm:px-6 md:px-8 py-8">
          {children}
        </main>
      </div>
    </div>
  );
};
