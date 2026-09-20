import React, { useState } from 'react';
import { supabase } from '../lib/supabase';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { AuthLayout } from '../components/auth/AuthLayout';
import { Field, FormError, PasswordInput, SubmitButton, TextInput } from '../components/auth/AuthFields';

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
    <AuthLayout
      title="Welcome back"
      lede="Sign in to your meeting workspace."
      footer={
        <>
          New to MeetIQ?{' '}
          <Link to="/register" className="font-bold text-accent-ink hover:opacity-80">
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={handleLogin} className="mt-6 flex flex-col gap-4">
        <FormError>{error}</FormError>

        <Field label="Email" id="email">
          <TextInput
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
          />
        </Field>

        <Field label="Password" id="password">
          <PasswordInput
            id="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </Field>

        <SubmitButton busy={loading}>
          {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : 'Sign in'}
        </SubmitButton>
      </form>
    </AuthLayout>
  );
};
