import React, { useEffect, useState } from 'react';
import { CreditCard, Loader2, Check, ExternalLink } from 'lucide-react';
import api from '../../lib/api';
import { openCheckout } from '../../lib/razorpay';
import { useAuth } from '../../context/AuthContext';
import { useBilling } from '../../context/BillingContext';
import { useToast } from '../../context/ToastContext';
import { PlanCards } from './PlanCards';
import { UsageMeter } from './UsageMeter';

/**
 * The plan section of Settings: what you are on, the other tiers, and the
 * upgrade flow.
 *
 * The flow, which is worth reading once before changing it:
 *
 *   POST /billing/orders   -> an order, and the public key for Checkout
 *   Razorpay Checkout       -> the user pays (test card, test keys)
 *   POST /billing/verify    -> the signature is checked server-side and the
 *                              plan is granted; the response IS the new
 *                              billing state, so nothing needs re-fetching
 *
 * Two rules this must keep:
 *
 * **Nothing here decides what anyone is charged or what they get.** The
 * amount comes from the catalogue server-side, and the plan is granted only
 * against a verified HMAC. This component sends a plan id and shows what came
 * back.
 *
 * **An abandoned checkout is not a failure.** Closing the dialog leaves the
 * order "created" and untouched - no error, no toast, nothing said. Treating
 * it as an error would shout at everyone who changes their mind.
 */
const formatRupees = (paise) => `₹${(paise / 100).toLocaleString('en-IN')}`;

const formatDate = (iso) =>
  iso ? new Date(iso).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }) : '';

export const BillingCard = () => {
  const { user } = useAuth();
  const { toast } = useToast();
  const { plan, planName, plans, subscription, usage, billingEnabled, loading, refresh } = useBilling();

  const [busyPlan, setBusyPlan] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [payments, setPayments] = useState([]);

  useEffect(() => {
    if (loading) return;
    api.get('/billing/payments')
      .then(({ data }) => setPayments(data))
      .catch(() => setPayments([]));
  }, [loading, plan]);

  const handleUpgrade = async (target) => {
    setBusyPlan(target.id);
    try {
      const { data: order } = await api.post('/billing/orders', { plan: target.id, seats: 1 });

      await openCheckout({
        order,
        email: user?.email,
        planName: target.name,
        onSuccess: async (signed) => {
          try {
            await api.post('/billing/verify', signed);
            await refresh();
            toast(`You're on ${target.name}.`);
          } catch (err) {
            // The money moved but verification failed. Say so honestly and
            // point at the one thing that resolves it - the webhook grants
            // the same plan independently, so this is usually a wait, not a
            // loss.
            toast(
              err.response?.data?.detail
                || 'Payment went through but could not be confirmed. Refresh in a moment.'
            );
          } finally {
            setBusyPlan(null);
          }
        },
        onDismiss: () => setBusyPlan(null),
        onError: (message) => {
          toast(message);
          setBusyPlan(null);
        },
      });
    } catch (err) {
      toast(err.response?.data?.detail || 'Could not start the payment. Please try again.');
      setBusyPlan(null);
    }
  };

  const handleCancel = async () => {
    setCancelling(true);
    try {
      await api.post('/billing/cancel');
      await refresh();
      toast('Cancelled. You keep your plan until the period ends.');
    } catch (err) {
      toast(err.response?.data?.detail || 'Could not cancel. Please try again.');
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="bg-surface border border-border-strong rounded-2xl p-6 flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <div className="bg-accent-soft p-2.5 rounded-xl">
          <CreditCard className="w-5 h-5 text-accent-ink" />
        </div>
        <div className="min-w-0">
          <h2 className="text-[15px] font-bold text-brand-dark">Plan &amp; billing</h2>
          <p className="text-xs text-muted">
            {loading ? 'Checking your plan…' : `You're on ${planName || 'Free'}.`}
          </p>
        </div>
      </div>

      {/* Razorpay runs on test keys for this demo. Saying so is not optional:
          a payment form that looks real and is not must announce itself. */}
      {billingEnabled && (
        <div className="rounded-xl border border-dashed border-border px-3.5 py-2.5 text-[12.5px] text-muted">
          Demo mode — payments use Razorpay test keys. Use card <span className="font-mono">4111 1111 1111 1111</span>,
          any future expiry and CVV. No real money moves.
        </div>
      )}

      {usage && (
        <div className="flex flex-col gap-3.5 rounded-xl border border-line bg-sidebar px-3.5 py-3.5">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-[13.5px] font-bold text-brand-dark">Usage {usage.period_label}</h3>
            <span className="text-[12px] text-muted">Resets {formatDate(usage.period_end)}</span>
          </div>
          <UsageMeter label="Meetings recorded" used={usage.meetings_used} limit={usage.meetings_limit} />
          <UsageMeter label="AI questions" used={usage.ai_questions_used} limit={usage.ai_questions_limit} />
          <p className="text-[12px] text-muted">
            Recordings are capped at {usage.max_duration_minutes} minutes each on your plan.
          </p>
        </div>
      )}

      {subscription && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-sidebar px-3.5 py-3">
          <div className="text-[13.5px] text-body">
            {subscription.cancel_at_period_end ? (
              <>Cancelled — active until <strong>{formatDate(subscription.current_period_end)}</strong></>
            ) : (
              <>Renews on <strong>{formatDate(subscription.current_period_end)}</strong></>
            )}
          </div>
          {!subscription.cancel_at_period_end && (
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="px-3 py-1.5 border border-border rounded-lg text-[13px] font-semibold text-faint transition-colors hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50"
            >
              {cancelling ? 'Cancelling…' : 'Cancel plan'}
            </button>
          )}
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-[13.5px] text-muted">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading plans…
        </div>
      ) : (
        <PlanCards
          plans={plans}
          currentPlan={plan}
          highlight="pro"
          renderAction={(target) => {
            if (target.id === plan) {
              return (
                <div className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-line text-[14.5px] font-bold text-muted">
                  <Check className="h-4 w-4" /> Current plan
                </div>
              );
            }
            if (target.price_paise === 0) {
              // Downgrading is not a purchase. Cancelling is the way back to
              // Free, so there is no button here that could pretend otherwise.
              return (
                <div className="inline-flex h-11 w-full items-center justify-center rounded-xl border border-line text-[13.5px] text-faint">
                  Cancel to return here
                </div>
              );
            }
            return (
              <button
                onClick={() => handleUpgrade(target)}
                disabled={!billingEnabled || busyPlan !== null}
                title={billingEnabled ? undefined : 'Payments are not configured on this deployment'}
                className={[
                  'inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl text-[14.5px] font-bold transition-opacity hover:opacity-90 disabled:opacity-50',
                  target.id === 'pro' ? 'btn-primary' : 'border border-border-strong bg-surface text-brand-dark',
                ].join(' ')}
              >
                {busyPlan === target.id ? (
                  <><Loader2 className="h-4 w-4 animate-spin" /> Opening…</>
                ) : (
                  <>Upgrade to {target.name}</>
                )}
              </button>
            );
          }}
        />
      )}

      {payments.length > 0 && (
        <div className="flex flex-col gap-2.5 pt-1">
          <h3 className="text-[13.5px] font-bold text-brand-dark">Billing history</h3>
          <div className="flex flex-col gap-1.5">
            {payments.map((payment) => (
              <div
                key={payment.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-line px-3.5 py-2.5 text-[13.5px]"
              >
                <span className="text-body">
                  {formatDate(payment.created_at)} · {payment.plan}
                  {payment.seats > 1 ? ` × ${payment.seats}` : ''}
                </span>
                <span className="flex items-center gap-2.5">
                  <span className="font-semibold text-brand-dark">{formatRupees(payment.amount_paise)}</span>
                  <span
                    className={[
                      'rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em]',
                      payment.status === 'paid'
                        ? 'bg-status-done-bg text-status-done-fg'
                        : 'bg-status-muted-bg text-status-muted-fg',
                    ].join(' ')}
                  >
                    {payment.status}
                  </span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="text-[12.5px] text-muted">
        Prices in INR.{' '}
        <a
          href="/terms"
          className="inline-flex items-center gap-1 font-semibold text-accent-ink hover:underline"
        >
          Terms <ExternalLink className="h-3 w-3" />
        </a>
      </p>
    </div>
  );
};
