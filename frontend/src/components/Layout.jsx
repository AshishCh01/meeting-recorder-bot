import React from 'react';
import { useAuth } from '../context/AuthContext';
import { Link, useLocation } from 'react-router-dom';
import { Bot, LayoutDashboard, LogOut } from 'lucide-react';

export const Layout = ({ children }) => {
  const { user, signOut } = useAuth();
  const location = useLocation();

  const navigation = [
    { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  ];

  return (
    <div className="min-h-screen bg-slate-50 flex">
      {/* Sidebar - Desktop */}
      <div className="hidden md:flex w-64 flex-col fixed inset-y-0 bg-white border-r border-slate-200 z-50">
        <div className="flex-1 flex flex-col pt-8 pb-4 overflow-y-auto">
          <div className="flex items-center px-6 mb-8 space-x-2">
            <div className="bg-brand-blue/10 p-2 rounded-xl">
              <Bot className="h-6 w-6 text-brand-blue" />
            </div>
            <span className="text-xl font-bold text-brand-dark tracking-tight">BOT<span className="text-brand-blue">.ai</span></span>
          </div>
          
          <nav className="mt-5 flex-1 px-4 space-y-1">
            {navigation.map((item) => {
              const isActive = location.pathname === item.href;
              return (
                <Link
                  key={item.name}
                  to={item.href}
                  className={`group flex items-center px-3 py-3 text-sm font-medium rounded-xl transition-colors ${
                    isActive
                      ? 'bg-brand-blue/10 text-brand-blue'
                      : 'text-slate-600 hover:bg-slate-50 hover:text-brand-dark'
                  }`}
                >
                  <item.icon
                    className={`mr-3 h-5 w-5 ${
                      isActive ? 'text-brand-blue' : 'text-slate-400 group-hover:text-slate-500'
                    }`}
                  />
                  {item.name}
                </Link>
              );
            })}
          </nav>
        </div>
        
        <div className="flex-shrink-0 flex flex-col p-4 border-t border-slate-100">
          <div className="flex items-center px-3 py-2 mb-2">
            <div className="w-8 h-8 rounded-full bg-slate-200 flex items-center justify-center text-slate-500 font-medium">
              {user?.email?.charAt(0).toUpperCase()}
            </div>
            <div className="ml-3 truncate">
              <p className="text-sm font-medium text-brand-dark truncate">{user?.email}</p>
            </div>
          </div>
          <button
            onClick={signOut}
            className="flex items-center w-full px-3 py-3 text-sm font-medium text-slate-600 rounded-xl hover:bg-slate-50 hover:text-brand-dark transition-colors"
          >
            <LogOut className="mr-3 h-5 w-5 text-slate-400" />
            Sign out
          </button>
        </div>
      </div>

      {/* Mobile top bar (simplified) */}
      <div className="md:hidden fixed top-0 w-full h-16 bg-white border-b border-slate-200 z-50 flex items-center justify-between px-4">
        <div className="flex items-center space-x-2">
          <div className="bg-brand-blue/10 p-1.5 rounded-lg">
            <Bot className="h-5 w-5 text-brand-blue" />
          </div>
          <span className="text-lg font-bold text-brand-dark tracking-tight">BOT<span className="text-brand-blue">.ai</span></span>
        </div>
        <button onClick={signOut} className="text-slate-500 p-2">
          <LogOut className="h-5 w-5" />
        </button>
      </div>

      {/* Main Content */}
      <div className="flex-1 md:ml-64 pt-16 md:pt-0 min-h-screen">
        <main className="max-w-7xl mx-auto px-4 sm:px-6 md:px-8 py-8">
          {children}
        </main>
      </div>
    </div>
  );
};
