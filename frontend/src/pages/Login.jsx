import React, { useState } from 'react';
import { supabase } from '../lib/supabase';
import { Link, useNavigate } from 'react-router-dom';
import { Mail, Lock, Loader2 } from 'lucide-react';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/Logo';

export const Login = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });

    if (error) {
      setError(error.message);
      setLoading(false);
    } else {
      navigate('/dashboard');
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-page">
      <header className="flex items-center justify-between px-5 sm:px-6 h-16">
        <Link to="/" className="flex items-center gap-2.5">
          <Logo alt="MeetIQ" className="h-7 w-7" />
          <span className="text-lg font-extrabold text-brand-dark tracking-tight">MeetIQ</span>
        </Link>
        <ThemeToggle className="p-2 text-muted hover:text-brand-dark transition-colors" iconClassName="h-5 w-5" />
      </header>

      <div className="flex-1 flex items-center justify-center px-4 pb-16">
        <div className="w-full max-w-md p-8 bg-surface border border-border-strong rounded-3xl shadow-xl shadow-slate-200/50 dark:shadow-none">
          <div className="text-center mb-8">
            <h1 className="text-2xl font-extrabold text-brand-dark tracking-tight mb-2">Welcome back</h1>
            <p className="text-body">Sign in to your meeting workspace</p>
          </div>

          {error && (
            <div className="mb-6 p-4 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-xl text-red-600 dark:text-red-400 text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleLogin} className="flex flex-col gap-5">
            <div>
              <label className="block text-sm font-medium text-body mb-1.5">Email</label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                  <Mail className="h-5 w-5 text-faint" />
                </div>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="block w-full pl-11 pr-4 py-3 bg-surface border border-border rounded-xl text-brand-dark placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-blue/20 focus:border-brand-blue transition-all"
                  placeholder="you@company.com"
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-body mb-1.5">Password</label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                  <Lock className="h-5 w-5 text-faint" />
                </div>
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="block w-full pl-11 pr-4 py-3 bg-surface border border-border rounded-xl text-brand-dark placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-blue/20 focus:border-brand-blue transition-all"
                  placeholder="••••••••"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 px-4 mt-1 flex justify-center items-center bg-linear-to-br from-brand-blue to-brand-blue-light hover:opacity-90 text-white font-bold rounded-xl shadow-lg shadow-brand-blue/25 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : 'Sign In'}
            </button>
          </form>

          <p className="mt-8 text-center text-sm text-body">
            Don't have an account?{' '}
            <Link to="/register" className="font-bold text-brand-blue hover:opacity-80 transition-opacity">
              Sign up
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
};
