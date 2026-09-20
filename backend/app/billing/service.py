"""
Subscription lifecycle - billing Phase 3.

razorpay_client.py talks to Razorpay. This module decides what a payment
*means*: which rows change, in what order, and what must not happen twice.

**Activation is idempotent, and that is the whole design.** A successful
checkout is reported twice by construction - once by the browser, which posts
the signature back to POST /billing/verify, and once by Razorpay's webhook,
which arrives independently and may be retried for days. Both call
activate_from_payment, and exactly one of them may extend the subscription.
The guard is the payment row's own status, moved created -> paid inside the
same transaction that writes the subscription. A second caller finds it
already "paid" and returns the existing subscription untouched. Without that,
a retried webhook silently buys the user another month.

**users.plan is written here and nowhere else.** It is a cache of the
subscription (see the column comment on User.plan), so it has exactly one
writer: this module, in the same commit that writes the subscription row it
is caching. quota.resolve() does not trust it anyway, which is the backstop
if this is ever got wrong.

**This is a demo on test keys.** A real system would need proration on an
upgrade, dunning on a failed renewal, refunds, and a reconciliation job
against Razorpay's settlement report. None of that exists. What does exist is
the part a demo has to get right: money is never taken without a verified
signature, and the same payment cannot be counted twice.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.billing import razorpay_client
from app.billing.plans import PURCHASABLE_PLAN_IDS, charge_paise, get_plan, is_purchasable
from app.db.models import Payment, Subscription, User

logger = logging.getLogger(__name__)

# One period. Thirty days rather than a calendar month, so every subscription
# is the same length whenever it starts and the renewal date is arithmetic
# rather than a question about February. quota.resolve() reads the window off
# the row, so nothing else in the app has to know this number.
PERIOD_DAYS = 30

# The most seats one Team subscription may be bought with. Not a business
# rule - a guard, so a typed or tampered seat count cannot turn into a
# hundred-thousand-rupee order.
MAX_SEATS = 100


class BillingError(RuntimeError):
    """Something the caller did wrong - the API turns this into a 400."""


def start_checkout(db: Session, user_id: str, plan_id: str, seats: int = 1) -> Payment:
    """
    Opens a Razorpay order for a plan and records the intent as a "created"
    payment row. Returns that row; the API hands its order id to Checkout.

    Nothing about the user's plan changes here. A created order is an
    intention to pay, and the overwhelming majority of the ones that are never
    completed should leave no trace on the account.
    """
    if not is_purchasable(plan_id):
        raise BillingError(
            "{} is not a plan you can buy. Choose one of: {}.".format(
                plan_id, ", ".join(sorted(PURCHASABLE_PLAN_IDS))
            )
        )

    plan = get_plan(plan_id)

    if not plan.per_seat:
        # Pro is one person. Quietly normalising rather than erroring: a
        # client that sends seats=3 for Pro is confused, not malicious, and
        # charge_paise ignores it anyway - this keeps the stored row honest.
        seats = 1
    if seats < 1 or seats > MAX_SEATS:
        raise BillingError("Seats must be between 1 and {}.".format(MAX_SEATS))

    amount_paise = charge_paise(plan, seats)

    # Short and unique - Razorpay caps receipt at 40 characters. The payment
    # row's own id would need a round-trip to exist first, so this is its own
    # value and lives only on Razorpay's side as a human-readable reference.
    receipt = "meetiq-{}".format(uuid.uuid4().hex[:24])

    order = razorpay_client.create_order(
        amount_paise=amount_paise,
        receipt=receipt,
        # Echoed back on the webhook, which is how an event that names only an
        # order can be attributed without a database read. Razorpay requires
        # note values to be strings.
        notes={"user_id": str(user_id), "plan": plan.id, "seats": str(seats)},
    )

    payment = Payment(
        user_id=user_id,
        plan=plan.id,
        seats=seats,
        amount_paise=amount_paise,
        currency="INR",
        status="created",
        razorpay_order_id=order["id"],
        notes={"receipt": receipt},
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    logger.info(
        "[billing] user %s started checkout for %s (%s paise, order %s)",
        user_id, plan.id, amount_paise, order["id"],
    )
    return payment


def _period(now: datetime) -> tuple:
    return now, now + timedelta(days=PERIOD_DAYS)


def activate_from_payment(
    db: Session, payment: Payment, razorpay_payment_id: Optional[str] = None,
    signature: Optional[str] = None, now: Optional[datetime] = None,
) -> Subscription:
    """
    Turns a paid order into a live subscription. Safe to call twice with the
    same payment - see the module docstring on why that is guaranteed to
    happen rather than merely possible.

    Everything here lands in one commit: the payment moves to "paid", the
    subscription row is written or extended, and users.plan is updated to
    match. A crash partway leaves none of it, which is the only state that
    cannot mislead - a "paid" payment with no subscription would be a customer
    charged for nothing.
    """
    now = now or datetime.now(timezone.utc)

    existing = (
        db.query(Subscription).filter(Subscription.user_id == payment.user_id).first()
    )

    if payment.status == "paid":
        # Already done - the other reporter got here first. Not an error, and
        # emphatically not another period.
        logger.info(
            "[billing] order %s is already paid - not activating twice",
            payment.razorpay_order_id,
        )
        return existing

    start, end = _period(now)

    if existing is None:
        subscription = Subscription(
            user_id=payment.user_id,
            plan=payment.plan,
            status="active",
            seats=payment.seats,
            current_period_start=start,
            current_period_end=end,
            razorpay_order_id=payment.razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
        )
        db.add(subscription)
    else:
        # Rewritten in place - subscriptions is not a history table (see the
        # model). An upgrade from Pro to Team takes effect now and restarts
        # the period; the unused remainder of the old one is not credited,
        # which is exactly the proration a demo does not implement.
        existing.plan = payment.plan
        existing.status = "active"
        existing.seats = payment.seats
        existing.current_period_start = start
        existing.current_period_end = end
        existing.cancel_at_period_end = False
        existing.razorpay_order_id = payment.razorpay_order_id
        existing.razorpay_payment_id = razorpay_payment_id
        subscription = existing

    payment.status = "paid"
    if razorpay_payment_id:
        payment.razorpay_payment_id = razorpay_payment_id
    if signature:
        payment.razorpay_signature = signature

    # The cache, written in the same commit as the thing it caches.
    db.query(User).filter(User.id == payment.user_id).update({"plan": payment.plan})

    db.commit()
    db.refresh(subscription)

    logger.info(
        "[billing] user %s is now on %s until %s (order %s)",
        payment.user_id, payment.plan, end.isoformat(), payment.razorpay_order_id,
    )
    return subscription


def confirm_checkout(
    db: Session, user_id: str, order_id: str, razorpay_payment_id: str, signature: str,
) -> Subscription:
    """
    The browser's report that Checkout succeeded.

    Three things are checked before a plan is granted, and the order matters:
    the order must exist and belong to this user, the signature must verify,
    and only then is anything activated. A caller who can forge none of these
    can post this endpoint all day and change nothing.
    """
    payment = (
        db.query(Payment).filter(Payment.razorpay_order_id == order_id).first()
    )
    if payment is None:
        raise BillingError("Unknown order.")

    if str(payment.user_id) != str(user_id):
        # Someone else's order. A 400 saying "unknown order" rather than a 403
        # saying "not yours", which would confirm the id is real.
        logger.warning(
            "[billing] user %s tried to confirm order %s, which belongs to %s",
            user_id, order_id, payment.user_id,
        )
        raise BillingError("Unknown order.")

    if not razorpay_client.verify_checkout_signature(order_id, razorpay_payment_id, signature):
        logger.warning(
            "[billing] signature did not verify for order %s (user %s)", order_id, user_id
        )
        _mark_failed(db, payment, "signature verification failed")
        raise BillingError("This payment could not be verified.")

    return activate_from_payment(
        db, payment, razorpay_payment_id=razorpay_payment_id, signature=signature
    )


def _mark_failed(db: Session, payment: Payment, reason: str) -> None:
    """
    Records a failure without touching the subscription.

    Only ever applied to a payment that has not been paid: a verified
    activation must not be undone by a later stray failure event for the same
    order, which Razorpay can send when a second attempt on the same order is
    abandoned.
    """
    if payment.status == "paid":
        logger.info(
            "[billing] ignoring a '%s' failure for order %s - it is already paid",
            reason, payment.razorpay_order_id,
        )
        return
    payment.status = "failed"
    notes = dict(payment.notes or {})
    notes["failure_reason"] = reason
    payment.notes = notes
    db.commit()


# Razorpay event names this service acts on. Anything else - and there are
# dozens - is acknowledged and ignored, so turning on a new event type in the
# dashboard cannot start failing webhooks.
PAID_EVENTS = frozenset({"payment.captured", "order.paid"})
FAILED_EVENTS = frozenset({"payment.failed"})


def handle_webhook_event(db: Session, event: dict) -> str:
    """
    Razorpay's own report of what happened, independent of the browser.

    This is the authoritative path: a user whose laptop closed between paying
    and the redirect still gets their plan, because this arrives anyway. It
    reaches the same activate_from_payment as the browser does, and whichever
    lands first wins.

    Returns a short string describing what was done, for the log and the
    response body. Never raises on an event it does not recognise - a webhook
    endpoint that 500s gets retried forever and eventually disabled.
    """
    event_name = event.get("event", "")
    entity = (
        event.get("payload", {}).get("payment", {}).get("entity")
        or event.get("payload", {}).get("order", {}).get("entity")
        or {}
    )
    order_id = entity.get("order_id") or entity.get("id")

    if not order_id:
        logger.info("[billing] webhook '%s' carried no order id - ignoring", event_name)
        return "ignored: no order id"

    payment = db.query(Payment).filter(Payment.razorpay_order_id == order_id).first()
    if payment is None:
        # An order this service never created. Most likely a webhook from
        # another integration on the same Razorpay account.
        logger.info("[billing] webhook '%s' named unknown order %s - ignoring", event_name, order_id)
        return "ignored: unknown order"

    if event_name in PAID_EVENTS:
        activate_from_payment(db, payment, razorpay_payment_id=entity.get("id"))
        return "activated"

    if event_name in FAILED_EVENTS:
        _mark_failed(db, payment, entity.get("error_description") or event_name)
        return "marked failed"

    logger.info("[billing] webhook '%s' is not an event this service acts on", event_name)
    return "ignored: {}".format(event_name)


def cancel_at_period_end(db: Session, user_id: str) -> Subscription:
    """
    Stops the subscription renewing without taking anything away now - the
    user keeps what they paid for until current_period_end, and expiry moves
    them to free when it arrives.

    There is no renewal job in this demo, so in practice every subscription
    already ends at current_period_end. This flag is what makes the UI able to
    say so honestly.
    """
    subscription = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if subscription is None:
        raise BillingError("You do not have a subscription to cancel.")

    subscription.cancel_at_period_end = True
    db.commit()
    db.refresh(subscription)
    logger.info("[billing] user %s cancelled - active until %s", user_id, subscription.current_period_end)
    return subscription


def expire_due_subscriptions(db: Session, now: Optional[datetime] = None) -> int:
    """
    Moves every subscription whose period has run out to "expired" and puts
    its user back on free. Returns how many were swept.

    This is tidying, not enforcement. quota.resolve() already treats an
    elapsed period as free at read time, so a sweep that never runs cannot
    give anyone a plan they have not paid for - it only means users.plan and
    the subscription's status stay stale, which the billing page would show.
    """
    now = now or datetime.now(timezone.utc)

    due = (
        db.query(Subscription)
        .filter(Subscription.status == "active", Subscription.current_period_end <= now)
        .all()
    )
    if not due:
        return 0

    from app.billing.plans import FREE

    for subscription in due:
        subscription.status = "expired"
        db.query(User).filter(User.id == subscription.user_id).update({"plan": FREE})
        logger.info(
            "[billing] subscription for user %s expired at %s - back to free",
            subscription.user_id, subscription.current_period_end.isoformat(),
        )

    db.commit()
    return len(due)
