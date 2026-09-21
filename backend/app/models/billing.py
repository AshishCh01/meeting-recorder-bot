"""
Request and response shapes for billing.

Phase 1 defined what describes a plan and a subscription. Phase 3 adds the
checkout pair: what the browser asks for, and what it needs to open Razorpay
Checkout.

Note what is *not* here: no request model carries an amount. The client names
a plan and a seat count, and the price is looked up server-side from the
catalogue - a browser that could name its own price would buy Team for one
rupee. See billing/service.start_checkout.
"""
from typing import Optional

from pydantic import BaseModel, Field


class PlanOut(BaseModel):
    """
    One tier, as the pricing page renders it.

    `None` on a limit means unlimited, matching app/billing/plans.py - the
    frontend must render that as "Unlimited", never as "0".
    """
    id: str
    name: str
    tagline: str
    price_paise: int
    price_rupees: int
    per_seat: bool
    meetings_per_period: Optional[int] = None
    # What the tier was designed to offer.
    max_duration_minutes: int
    # What a recording actually gets here, once this deployment's recorder
    # cap is applied. THIS is the number the pricing page prints - see
    # plans.Plan.to_public_dict.
    effective_max_duration_minutes: int
    ask_ai_questions_per_period: Optional[int] = None
    pdf_export: bool
    calendar_scheduling: bool
    team_workspace: bool
    early_access: bool


class SubscriptionOut(BaseModel):
    """
    A user's current subscription. Absent entirely for a free user, who has
    no subscription row - the billing page shows the free tier when this is
    null rather than inventing a fake "free subscription".

    Carries no Razorpay ids: they are for tracing a row back to the
    dashboard, and the browser has no use for them.
    """
    plan: str
    status: str
    seats: int
    current_period_start: str
    current_period_end: str
    cancel_at_period_end: bool


class PaymentOut(BaseModel):
    """One row of billing history."""
    id: str
    plan: str
    seats: int
    amount_paise: int
    currency: str
    status: str
    created_at: str


class CheckoutRequest(BaseModel):
    """
    What the upgrade button sends. A plan id and, for a per-seat plan, how
    many seats - never an amount.
    """
    plan: str
    seats: int = Field(default=1, ge=1, le=100)


class CheckoutOrder(BaseModel):
    """
    Everything Razorpay Checkout needs to open in the browser.

    `key_id` is the public half of the key pair and is meant to be here; the
    key secret and the webhook secret must never appear in a response.
    `amount_paise` is what the server decided, echoed back only so the UI can
    show the figure it is about to charge.
    """
    order_id: str
    amount_paise: int
    currency: str
    key_id: str
    plan: str
    seats: int
    # True when the configured key is a test key, so the UI can label the
    # demo as a demo rather than implying real money is moving.
    test_mode: bool


class CheckoutConfirmation(BaseModel):
    """
    What Razorpay Checkout hands back to the browser on success, posted
    straight through to be verified. The three fields are signed together -
    see razorpay_client.verify_checkout_signature.
    """
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class UsageOut(BaseModel):
    """
    The usage meters. A null limit means unlimited and must be rendered as
    "Unlimited", never as 0.
    """
    plan: str
    plan_name: str
    period_start: str
    period_end: str
    period_label: str
    meetings_used: int
    meetings_limit: Optional[int] = None
    ai_questions_used: int
    ai_questions_limit: Optional[int] = None
    max_duration_minutes: int


class BillingStateOut(BaseModel):
    """
    One call for everything the billing page renders: the catalogue, where the
    user currently is, and what they have used.
    """
    plans: list[PlanOut]
    current_plan: str
    subscription: Optional[SubscriptionOut] = None
    usage: UsageOut
    # False when Razorpay is not configured - the UI shows the plans but
    # disables the upgrade buttons rather than opening a checkout that cannot
    # work.
    billing_enabled: bool
