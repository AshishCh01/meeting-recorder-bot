import React, { useState } from 'react';
import { supabase } from '../lib/supabase';
import { Link, useNavigate } from 'react-router-dom';
import { Mail, Lock, Loader2, Bot } from 'lucide-react';

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
    <div className="min-h-screen flex items-center justify-center bg-slate-50 relative overflow-hidden">
      {/* Decorative background subtle gradients */}
      <div className="absolute top-[-10%] left-[-10%] w-96 h-96 bg-brand-blue rounded-full mix-blend-multiply filter blur-3xl opacity-5 animate-blob"></div>
      
      <div className="relative z-10 w-full max-w-md p-8 bg-white border border-slate-100 rounded-3xl shadow-xl shadow-slate-200/50">
        <div className="text-center mb-10">
          <div className="flex items-center justify-center space-x-2 mb-6">
            <div className="bg-brand-blue/10 p-3 rounded-2xl">
              <Bot className="h-8 w-8 text-brand-blue" />
            </div>
            <span className="text-2xl font-bold text-brand-dark tracking-tight">BOT<span className="text-brand-blue">.ai</span></span>
          </div>
          <h2 className="text-2xl font-semibold text-brand-dark mb-2 tracking-tight">Welcome back</h2>
          <p className="text-slate-500">Sign in to your meeting workspace</p>
        </div>
        
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-100 rounded-xl text-red-600 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleLogin} className="space-y-5">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">Email</label>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                <Mail className="h-5 w-5 text-slate-400" />
              </div>
              <input 
                type="email" 
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="block w-full pl-11 pr-4 py-3 bg-white border border-slate-200 rounded-xl text-brand-dark placeholder-slate-400 focus:ring-2 focus:ring-brand-blue/20 focus:border-brand-blue transition duration-200"
                placeholder="you@example.com"
              />
            </div>
          </div>
          
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">Password</label>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                <Lock className="h-5 w-5 text-slate-400" />
              </div>
              <input 
                type="password" 
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="block w-full pl-11 pr-4 py-3 bg-white border border-slate-200 rounded-xl text-brand-dark placeholder-slate-400 focus:ring-2 focus:ring-brand-blue/20 focus:border-brand-blue transition duration-200"
                placeholder="••••••••"
              />
            </div>
          </div>

          <button 
            type="submit" 
            disabled={loading}
            className="w-full py-3 px-4 mt-2 flex justify-center items-center bg-brand-blue hover:opacity-90 text-white font-medium rounded-xl shadow-lg shadow-brand-blue/25 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : 'Sign In'}
          </button>
        </form>

        <p className="mt-8 text-center text-sm text-slate-500">
          Don't have an account?{' '}
          <Link to="/register" className="font-medium text-brand-blue hover:opacity-80 transition-opacity">
            Sign up
          </Link>
        </p>
      </div>
    </div>
  );
};
