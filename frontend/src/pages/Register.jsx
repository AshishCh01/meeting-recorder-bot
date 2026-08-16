import React, { useState } from 'react';
import { supabase } from '../lib/supabase';
import { Link, useNavigate } from 'react-router-dom';
import { Mail, Lock, Loader2, Bot } from 'lucide-react';

export const Register = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);
  const navigate = useNavigate();

  const handleRegister = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { error } = await supabase.auth.signUp({ email, password });
    
    if (error) {
      setError(error.message);
      setLoading(false);
    } else {
      setSuccess(true);
      setLoading(false);
      setTimeout(() => navigate('/dashboard'), 2000);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 relative overflow-hidden">
      <div className="absolute bottom-[-10%] right-[-10%] w-96 h-96 bg-brand-blue rounded-full mix-blend-multiply filter blur-3xl opacity-5 animate-blob"></div>
      
      <div className="relative z-10 w-full max-w-md p-8 bg-white border border-slate-100 rounded-3xl shadow-xl shadow-slate-200/50">
        <div className="text-center mb-10">
          <div className="flex items-center justify-center space-x-2 mb-6">
            <div className="bg-brand-blue/10 p-3 rounded-2xl">
              <Bot className="h-8 w-8 text-brand-blue" />
            </div>
            <span className="text-2xl font-bold text-brand-dark tracking-tight">BOT<span className="text-brand-blue">.ai</span></span>
          </div>
          <h2 className="text-2xl font-semibold text-brand-dark mb-2 tracking-tight">Create Account</h2>
          <p className="text-slate-500">Join to start recording your meetings</p>
        </div>
        
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-100 rounded-xl text-red-600 text-sm">
            {error}
          </div>
        )}
        
        {success && (
          <div className="mb-6 p-4 bg-green-50 border border-green-100 rounded-xl text-green-700 text-sm">
            Registration successful! Redirecting...
          </div>
        )}

        <form onSubmit={handleRegister} className="space-y-5">
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
            disabled={loading || success}
            className="w-full py-3 px-4 mt-2 flex justify-center items-center bg-brand-blue hover:bg-blue-700 text-white font-medium rounded-xl shadow-lg shadow-brand-blue/25 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : 'Sign Up'}
          </button>
        </form>

        <p className="mt-8 text-center text-sm text-slate-500">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-brand-blue hover:text-blue-700 transition-colors">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
};
