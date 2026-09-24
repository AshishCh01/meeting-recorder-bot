import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import api from '../lib/api';
import { useAuth } from './AuthContext';

/**
 * What plan the signed-in user is on, what it includes, and what they have
 * used - fetched once from GET /billing and shared, so a page that wants to
 * gate a button does not make its own request for it.
 *
 * Two rules this follows, both of which matter more than they look:
 *
 * **The backend is the authority; this is only for what the UI shows.** Every
 * gated route enforces the same plan server-side (billing Phase 4), so hiding
 * a button here is a courtesy, not a control. Nothing may be built on the
 * assumption that a user cannot call an endpoint just because this hid its
 * button.
 *
 * **An unknown plan grants nothing.** While the fetch is in flight, or if it
 * fails outright, `can()` returns false. Defaulting the other way would flash
 * a paid button to every free user on every page load, and then fail their
 * click with a 402 - worse than a moment of nothing.
 */
// Mirrors app/billing/plans.py. `null` means unlimited, never zero.
const EMPTY = {
  plan: null,
  planName: null,
  features: {},
  usage: null,
  subscription: null,
  plans: [],
  billingEnabled: false,
};

/**
 * The default is a whole, usable value rather than `{}`.
 *
 * A consumer can genuinely receive it: during a hot reload the provider and
 * the consumer can briefly hold two different module instances of this file,
 * and useContext then falls back to this default. With `{}` that meant
 * `plans` was undefined and `can` was not a function, so the first
 * `plans.map(...)` threw and took the whole page down with it - a white
 * screen from a dev-server artefact rather than from anything real.
 *
 * Shaped like the ungranted state, so the worst case is a page that shows
 * nothing paid until the next render, which is also the correct behaviour
 * when a provider really is missing.
 */
const BillingContext = createContext({
  ...EMPTY,
  loading: true,
  refresh: () => {},
  can: () => false,
});

export const BillingProvider = ({ children }) => {
  const { session } = useAuth();
  const [state, setState] = useState(EMPTY);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!session) {
      // Signed out - the landing page renders this tree too, and /billing
      // would 401. Not an error state, just nothing to know yet.
      setState(EMPTY);
      setLoading(false);
      return;
    }
    try {
      const { data } = await api.get('/billing');
      setState({
        plan: data.current_plan,
        planName: data.usage?.plan_name === 'Team' ? 'Premium' : (data.usage?.plan_name ?? null),
        // The flags live on the plan entry for the plan the user is on, so a
        // gate reads features.pdf_export rather than comparing plan ids - the
        // same shape the backend gates use.
        features: data.plans?.find((p) => p.id === data.current_plan) ?? {},
        usage: data.usage ?? null,
        subscription: data.subscription ?? null,
        plans: data.plans ? data.plans.map(p => p.name === 'Team' ? { ...p, name: 'Premium' } : p) : [],
        billingEnabled: Boolean(data.billing_enabled),
      });
    } catch {
      // Leave the UI in its ungranted state rather than guessing. The gated
      // endpoints still refuse properly, so the worst case is a hidden button
      // on a plan that would have allowed it - recoverable with a reload,
      // unlike a button that 402s when pressed.
      setState(EMPTY);
    } finally {
      setLoading(false);
    }
  }, [session]);

  useEffect(() => {
    setLoading(true);
    refresh();
  }, [refresh]);

  const value = useMemo(
    () => ({
      ...state,
      loading,
      refresh,
      /** Whether the current plan includes a feature, e.g. can('pdf_export'). */
      can: (feature) => Boolean(state.features?.[feature]),
    }),
    [state, loading, refresh]
  );

  return <BillingContext.Provider value={value}>{children}</BillingContext.Provider>;
};

export const useBilling = () => useContext(BillingContext);
