import React, { useState } from 'react';
import { supabase } from '../lib/supabase';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { AuthLayout } from '../components/auth/AuthLayout';
import { Field, FormError, PasswordInput, SubmitButton, TextInput } from '../components/auth/AuthFields';

export const Register = () => {
  const [searchParams] = useSearchParams();
  const [email, setEmail] = useState(searchParams.get('email') || '');
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
    <AuthLayout
      title="Create your account"
      // Was "5 meetings a month free" - there is no quota and no metering to
      // enforce one, and pricing is now "free while in beta".
      lede="Free while in beta · no card required."
      footer={
        <>
          Already have an account?{' '}
          <Link to="/login" className="font-bold text-accent-ink hover:opacity-80">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleRegister} className="mt-6 flex flex-col gap-4">
        <FormError>{error}</FormError>

        {success && (
          <div
            role="status"
            className="rounded-xl border border-accent-line bg-accent-soft p-3.5 text-sm text-accent-ink"
          >
            Account created. Taking you to your meetings…
          </div>
        )}

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
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </Field>

        <SubmitButton busy={loading || success}>
          {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : 'Create account'}
        </SubmitButton>
      </form>
    </AuthLayout>
  );
};
