"""
Billing endpoints - Phase 3.

The shape of the flow, which is worth reading once before changing anything:

    POST /billing/orders    browser asks for a plan -> we open a Razorpay
                            order and record a "created" payment
    (Razorpay Checkout opens in the browser, user pays with a test card)
    POST /billing/verify    browser reports success -> signature checked,
                            plan granted
    POST /billing/webhook   Razorpay reports the same success independently
                            -> same plan granted, or granted for the first
                            time if the browser never came back

The last two are two reports of one event and both are expected. Activation
is idempotent by design - see billing/service.py.

**This is a demo on Razorpay test keys.** Any signed-in user may buy any plan,
no real money moves, and there is no renewal, proration or refund path.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.billing import quota, razorpay_client, service
from app.billing.plans import ALL_PLANS, get_plan
from app.config import settings
from app.db.database import get_db
from app.db.models import Payment, Subscription
from app.models.billing import (
    BillingStateOut,
    CheckoutConfirmation,
    CheckoutOrder,
    CheckoutRequest,
    PaymentOut,
    PlanOut,
    SubscriptionOut,
    UsageOut,
)

router = APIRouter(prefix="/billing", tags=["billing"])

logger = logging.getLogger(__name__)


def _iso(value) -> str:
    return value.isoformat() if value is not None else None


def _subscription_out(row: Subscription):
    if row is None:
        return None
    return SubscriptionOut(
        plan=row.plan,
        status=row.status,
        seats=row.seats,
        current_period_start=_iso(row.current_period_start),
        current_period_end=_iso(row.current_period_end),
        cancel_at_period_end=row.cancel_at_period_end,
    )


def _usage_out(usage: quota.Usage) -> UsageOut:
    return UsageOut(
        plan=usage.plan.id,
        plan_name=usage.plan.name,
        period_start=_iso(usage.window.start),
        period_end=_iso(usage.window.end),
        period_label=usage.window.label,
        meetings_used=usage.meetings_used,
        meetings_limit=usage.meetings_limit,
        ai_questions_used=usage.ai_questions_used,
        ai_questions_limit=usage.ai_questions_limit,
        max_duration_minutes=usage.max_duration_minutes,
    )


@router.get("/plans", response_model=list[PlanOut])
def list_plans():
    """
    The catalogue. Deliberately unauthenticated - this is what the public
    pricing page renders, and there is nothing in a plan that is not already
    printed on it.
    """
    return [PlanOut(**plan.to_public_dict()) for plan in ALL_PLANS]


@router.get("", response_model=BillingStateOut)
def get_billing_state(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """
    Everything the billing page needs, in one call: the catalogue, the user's
    subscription (null on free), and their usage this period.
    """
    usage = quota.snapshot(db, user_id)
    subscription = db.query(Subscription).filter(Subscription.user_id == user_id).first()

    return BillingStateOut(
        plans=[PlanOut(**plan.to_public_dict()) for plan in ALL_PLANS],
        # The *effective* plan from quota.resolve, not users.plan - an expired
        # subscription must show as free here exactly as it is enforced.
        current_plan=usage.plan.id,
        subscription=_subscription_out(subscription),
        usage=_usage_out(usage),
        billing_enabled=settings.billing_enabled,
    )


@router.get("/payments", response_model=list[PaymentOut])
def list_payments(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """
    This user's billing history, newest first. Only rows that reached a
    terminal state are shown: an abandoned checkout is not a transaction, and
    listing every "created" order would make the history mostly noise.
    """
    rows = (
        db.query(Payment)
        .filter(Payment.user_id == user_id, Payment.status.in_(("paid", "failed")))
        .order_by(Payment.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        PaymentOut(
            id=str(row.id),
            plan=row.plan,
            seats=row.seats,
            amount_paise=row.amount_paise,
            currency=row.currency,
            status=row.status,
            created_at=_iso(row.created_at),
        )
        for row in rows
    ]


@router.post("/orders", response_model=CheckoutOrder)
def create_checkout_order(
    payload: CheckoutRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """
    Opens a Razorpay order for a plan. Nothing about the user's account
    changes here - this is an intention to pay, not a payment.

    The amount is computed from the catalogue, never read from the request.
    """
    try:
        payment = service.start_checkout(db, user_id, payload.plan, payload.seats)
    except service.BillingError as e:
        raise HTTPException(400, str(e))
    except razorpay_client.RazorpayNotConfigured as e:
        # 503, not 500: the service is fine, this feature is switched off.
        raise HTTPException(503, str(e))
    except razorpay_client.RazorpayError as e:
        raise HTTPException(502, str(e))

    return CheckoutOrder(
        order_id=payment.razorpay_order_id,
        amount_paise=payment.amount_paise,
        currency=payment.currency,
        # The public half of the key pair, which Checkout needs in the
        # browser. The secret never appears in a response.
        key_id=settings.razorpay_key_id,
        plan=payment.plan,
        seats=payment.seats,
        test_mode=settings.razorpay_is_test_mode,
    )


@router.post("/verify", response_model=BillingStateOut)
def verify_checkout(
    payload: CheckoutConfirmation,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """
    The browser's report that Checkout succeeded. The signature is what makes
    this trustworthy - without it, this endpoint would hand out plans to
    anyone who could name an order id.

    Returns the whole billing state so the UI can re-render from one response
    instead of paying for a second round-trip to see its new plan.
    """
    try:
        service.confirm_checkout(
            db,
            user_id,
            order_id=payload.razorpay_order_id,
            razorpay_payment_id=payload.razorpay_payment_id,
            signature=payload.razorpay_signature,
        )
    except service.BillingError as e:
        raise HTTPException(400, str(e))

    return get_billing_state(db=db, user_id=user_id)


@router.post("/cancel", response_model=BillingStateOut)
def cancel_subscription(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """
    Stops the subscription renewing. Nothing is taken away now - the user
    keeps the plan until the period ends.
    """
    try:
        service.cancel_at_period_end(db, user_id)
    except service.BillingError as e:
        raise HTTPException(400, str(e))
    return get_billing_state(db=db, user_id=user_id)


@router.post("/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Razorpay's own report of a payment, independent of the browser.

    Not authenticated the way the rest of the API is - the caller is Razorpay,
    not a signed-in user, and it proves itself with an HMAC over the raw body.
    That check is the entire security boundary here: this endpoint can grant a
    paid plan, so an unverified request must be refused, never trusted.

    The body is read as raw bytes and verified before it is parsed. Verifying
    a re-serialised dict would never match - JSON round-tripping reorders keys
    and changes whitespace.

    Always returns 200 once the signature verifies, whatever the event turns
    out to be. A webhook endpoint that errors on an event it does not handle
    gets retried forever and is eventually disabled by the provider.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not razorpay_client.verify_webhook_signature(raw_body, signature):
        logger.warning("[billing] rejected a webhook with an invalid signature")
        raise HTTPException(401, "Invalid webhook signature")

    import json

    try:
        event = json.loads(raw_body)
    except ValueError:
        logger.warning("[billing] a signed webhook carried a body that is not JSON")
        raise HTTPException(400, "Body is not JSON")

    outcome = service.handle_webhook_event(db, event)
    logger.info("[billing] webhook '%s' -> %s", event.get("event"), outcome)
    return {"status": "ok", "outcome": outcome}
