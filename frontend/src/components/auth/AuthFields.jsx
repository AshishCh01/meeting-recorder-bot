import React, { useState } from 'react';

// 16px (text-base) on every input, deliberately: iOS Safari zooms the page
// when a focused field is smaller, and on the auth screens that zoom lands
// the user mid-form with the submit button off-screen.
const INPUT =
  'h-11 w-full rounded-xl border border-border bg-surface px-3.5 text-base text-brand-dark placeholder:text-muted transition-colors focus:border-brand-blue focus:outline-none disabled:opacity-50';

export const Field = ({ label, id, children }) => (
  <div>
    <label htmlFor={id} className="mb-1.5 block text-[13px] font-bold text-brand-dark">
      {label}
    </label>
    {children}
  </div>
);

export const TextInput = ({ id, ...props }) => <input id={id} className={INPUT} {...props} />;

/**
 * Password input with a show/hide toggle.
 *
 * The button is inside the field's padding rather than beside it, so revealing
 * the password never reflows the form. `autoComplete` is passed in because the
 * right value differs between signing in and signing up, and getting it wrong
 * makes password managers offer the wrong thing.
 */
export const PasswordInput = ({ id, ...props }) => {
  const [visible, setVisible] = useState(false);

  return (
    <div className="relative">
      <input id={id} type={visible ? 'text' : 'password'} className={`${INPUT} pr-16`} {...props} />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-pressed={visible}
        aria-label={visible ? 'Hide password' : 'Show password'}
        className="absolute inset-y-0 right-0 px-3.5 text-[12.5px] font-bold text-muted transition-colors hover:text-brand-dark"
      >
        {visible ? 'Hide' : 'Show'}
      </button>
    </div>
  );
};

export const SubmitButton = ({ busy, children }) => (
  <button
    type="submit"
    disabled={busy}
    className="btn-primary mt-1 flex h-12 w-full items-center justify-center rounded-xl text-[15px] font-bold transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
  >
    {children}
  </button>
);

export const FormError = ({ children }) =>
  children ? (
    <div
      role="alert"
      className="rounded-xl border border-status-failed-fg/20 bg-status-failed-bg p-3.5 text-sm text-status-failed-fg"
    >
      {children}
    </div>
  ) : null;
