import React from 'react';
import { Check, Minus } from 'lucide-react';

/**
 * The three tiers, rendered the same way on the public pricing section and in
 * Settings. One component so the two can never drift into quoting different
 * numbers at the same person.
 *
 * Everything shown comes from GET /billing/plans - the backend catalogue in
 * app/billing/plans.py is the only place a price or a limit is written down.
 * Nothing here hardcodes "30 meetings" or "₹500".
 */

/** `null` is unlimited, and must never render as "0". */
const limitLabel = (value, noun) =>
  value === null || value === undefined ? `Unlimited ${noun}` : `${value} ${noun}`;

/**
 * The lines under each tier, derived from the plan object rather than written
 * out per tier - a new flag in the catalogue shows up here without an edit.
 *
 * Duration uses effective_max_duration_minutes, not max_duration_minutes: the
 * first is what a recording actually gets on this deployment, the second is
 * what the tier was designed for. Printing the design number while the
 * recorder enforces the other is how a pricing page ends up lying.
 */
function featuresOf(plan) {
  return [
    { on: true, text: limitLabel(plan.meetings_per_period, 'meetings a month') },
    { on: true, text: `Up to ${plan.effective_max_duration_minutes} min per recording` },
    { on: true, text: 'Transcript, summary and action items' },
    { on: true, text: limitLabel(plan.ask_ai_questions_per_period, 'AI questions a month') },
    { on: plan.pdf_export, text: 'PDF export' },
    { on: plan.calendar_scheduling, text: 'Calendar scheduling' },
    { on: plan.team_workspace, text: 'Shared team workspace' },
  ];
}

const PriceLine = ({ plan }) => {
  if (plan.price_paise === 0) {
    return <div className="text-[2rem] font-extrabold tracking-[-0.03em] text-brand-dark">Free</div>;
  }
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-[2rem] font-extrabold tracking-[-0.03em] text-brand-dark">
        ₹{plan.price_rupees.toLocaleString('en-IN')}
      </span>
      <span className="text-[13.5px] font-semibold text-muted">
        /month
      </span>
    </div>
  );
};

/**
 * @param plans        from GET /billing/plans
 * @param currentPlan  the viewer's plan id, or null when signed out
 * @param highlight    which tier gets the accent border - the one being sold
 * @param renderAction (plan) => node; the caller owns the button, because
 *                     "Create your account" and "Upgrade" are different jobs
 */
export const PlanCards = ({ plans = [], currentPlan = null, highlight = 'pro', renderAction }) => (
  <div className="grid gap-4 md:grid-cols-3">
    {plans.map((plan) => {
      const isCurrent = plan.id === currentPlan;
      const featured = plan.id === highlight;
      return (
        <div
          key={plan.id}
          className={[
            'flex flex-col gap-5 rounded-2xl border bg-surface p-6',
            featured ? 'border-accent-line ring-1 ring-accent-line' : 'border-border-strong',
          ].join(' ')}
        >
          <div className="flex flex-col gap-2.5">
            <div className="flex items-center gap-2">
              <h3 className="text-[15px] font-bold text-brand-dark">{plan.name}</h3>
              {featured && !isCurrent && (
                <span className="rounded-full bg-accent-soft px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-accent-ink">
                  Popular
                </span>
              )}
              {isCurrent && (
                <span className="rounded-full bg-accent-soft px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-accent-ink">
                  Current
                </span>
              )}
            </div>
            <PriceLine plan={plan} />
            <p className="text-[13.5px] leading-relaxed text-muted">{plan.tagline}</p>
          </div>

          <ul className="flex flex-col gap-2.5">
            {featuresOf(plan).map((feature) => (
              <li
                key={feature.text}
                className={[
                  'flex items-start gap-2.5 text-[13.5px] leading-snug',
                  // Excluded lines stay visible rather than being dropped:
                  // what a cheaper tier does NOT include is exactly what the
                  // reader is trying to find out.
                  feature.on ? 'text-body' : 'text-faint',
                ].join(' ')}
              >
                {feature.on ? (
                  <Check className="mt-0.5 h-3.5 w-3.5 flex-none text-accent-ink" strokeWidth={3} />
                ) : (
                  <Minus className="mt-0.5 h-3.5 w-3.5 flex-none text-faint" strokeWidth={3} />
                )}
                <span>{feature.text}</span>
              </li>
            ))}
          </ul>

          <div className="mt-auto pt-1">{renderAction?.(plan)}</div>
        </div>
      );
    })}
  </div>
);
