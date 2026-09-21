"""
The Razorpay checkout flow - billing Phase 3.

Razorpay itself is never called: httpx.post is stubbed, and signatures are
computed with the same HMAC the real Checkout uses, so the verification path
under test is the real one rather than a mock of it.

The cases that matter, in the order they matter:
  1. a forged or absent signature grants nothing
  2. the same payment cannot be activated twice (the browser and the webhook
     both report success, by design)
  3. the amount comes from the catalogue, never from the client
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.billing import razorpay_client, service
from app.billing.plans import FREE, PRO, TEAM
from app.config import settings
from app.db.models import Payment, Subscription, User

KEY_ID = "rzp_test_stubkey"
KEY_SECRET = "stub-secret-not-a-real-one"
WEBHOOK_SECRET = "stub-webhook-secret"


@pytest.fixture(autouse=True)
def razorpay_keys(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", KEY_ID)
    monkeypatch.setattr(settings, "razorpay_key_secret", KEY_SECRET)
    monkeypatch.setattr(settings, "razorpay_webhook_secret", WEBHOOK_SECRET)


@pytest.fixture
def fake_orders(monkeypatch):
    """
    Stands in for Razorpay's order API. Records what was sent so a test can
    assert on the amount that left this service.
    """
    sent = []

    class FakeResponse:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def fake_post(url, json=None, auth=None, timeout=None):
        sent.append({"url": url, "body": json, "auth": auth})
        return FakeResponse({
            "id": "order_{}".format(uuid.uuid4().hex[:14]),
            "amount": json["amount"],
            "currency": json["currency"],
            "receipt": json["receipt"],
            "status": "created",
        })

    monkeypatch.setattr(razorpay_client.httpx, "post", fake_post)
    return sent


def _user(db, email="checkout@example.com"):
    row = User(email=email)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _checkout_signature(order_id, payment_id, secret=KEY_SECRET):
    """Exactly what Razorpay Checkout computes and hands to the browser."""
    return hmac.new(
        secret.encode(), "{}|{}".format(order_id, payment_id).encode(), hashlib.sha256
    ).hexdigest()


def _webhook_signature(raw_body, secret=WEBHOOK_SECRET):
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


# ------------------------------------------------------------- pricing


def test_the_amount_comes_from_the_catalogue(db, fake_orders):
    user = _user(db)
    payment = service.start_checkout(db, str(user.id), PRO)

    assert payment.amount_paise == 50_000
    assert fake_orders[0]["body"]["amount"] == 50_000
    assert fake_orders[0]["body"]["currency"] == "INR"
    # HTTP Basic with the key pair - the secret authenticates, it is not sent
    # in the body.
    assert fake_orders[0]["auth"] == (KEY_ID, KEY_SECRET)


def test_team_is_priced_per_seat(db, fake_orders):
    user = _user(db, "seats@example.com")
    payment = service.start_checkout(db, str(user.id), TEAM, seats=4)
    assert payment.amount_paise == 480_000
    assert payment.seats == 4


def test_seats_are_ignored_for_a_flat_plan(db, fake_orders):
    user = _user(db, "flat@example.com")
    payment = service.start_checkout(db, str(user.id), PRO, seats=9)
    assert payment.amount_paise == 50_000
    assert payment.seats == 1


def test_free_cannot_be_bought(db, fake_orders):
    user = _user(db, "free-buy@example.com")
    with pytest.raises(service.BillingError):
        service.start_checkout(db, str(user.id), FREE)
    assert fake_orders == []


def test_an_absurd_seat_count_is_refused(db, fake_orders):
    user = _user(db, "many-seats@example.com")
    with pytest.raises(service.BillingError):
        service.start_checkout(db, str(user.id), TEAM, seats=10_000)
    assert fake_orders == []


def test_opening_a_checkout_does_not_grant_anything(db, fake_orders):
    """An intention to pay is not a payment."""
    user = _user(db, "intent@example.com")
    service.start_checkout(db, str(user.id), PRO)

    db.refresh(user)
    assert user.plan == FREE
    assert db.query(Subscription).filter(Subscription.user_id == user.id).first() is None


# --------------------------------------------------------- verification


def test_a_valid_signature_grants_the_plan(db, fake_orders):
    user = _user(db, "valid-sig@example.com")
    payment = service.start_checkout(db, str(user.id), PRO)
    payment_id = "pay_stub123"

    subscription = service.confirm_checkout(
        db, str(user.id),
        order_id=payment.razorpay_order_id,
        razorpay_payment_id=payment_id,
        signature=_checkout_signature(payment.razorpay_order_id, payment_id),
    )

    assert subscription.plan == PRO
    assert subscription.status == "active"
    db.refresh(user)
    assert user.plan == PRO

    db.refresh(payment)
    assert payment.status == "paid"
    assert payment.razorpay_payment_id == payment_id


def test_a_forged_signature_grants_nothing(db, fake_orders):
    user = _user(db, "forged@example.com")
    payment = service.start_checkout(db, str(user.id), PRO)

    with pytest.raises(service.BillingError):
        service.confirm_checkout(
            db, str(user.id),
            order_id=payment.razorpay_order_id,
            razorpay_payment_id="pay_forged",
            signature="deadbeef" * 8,
        )

    db.refresh(user)
    assert user.plan == FREE
    assert db.query(Subscription).filter(Subscription.user_id == user.id).first() is None
    db.refresh(payment)
    assert payment.status == "failed"


def test_a_signature_from_the_wrong_secret_is_refused(db, fake_orders):
    """The secret is the whole boundary - a near-miss must not pass."""
    user = _user(db, "wrong-secret@example.com")
    payment = service.start_checkout(db, str(user.id), PRO)

    with pytest.raises(service.BillingError):
        service.confirm_checkout(
            db, str(user.id),
            order_id=payment.razorpay_order_id,
            razorpay_payment_id="pay_x",
            signature=_checkout_signature(payment.razorpay_order_id, "pay_x", secret="not-the-secret"),
        )
    db.refresh(user)
    assert user.plan == FREE


def test_you_cannot_confirm_someone_elses_order(db, fake_orders):
    buyer = _user(db, "buyer@example.com")
    attacker = _user(db, "attacker@example.com")
    payment = service.start_checkout(db, str(buyer.id), TEAM)
    payment_id = "pay_stolen"

    with pytest.raises(service.BillingError):
        service.confirm_checkout(
            db, str(attacker.id),
            order_id=payment.razorpay_order_id,
            razorpay_payment_id=payment_id,
            # Even with a signature that genuinely verifies.
            signature=_checkout_signature(payment.razorpay_order_id, payment_id),
        )

    db.refresh(attacker)
    assert attacker.plan == FREE


def test_an_unknown_order_is_refused(db, fake_orders):
    user = _user(db, "unknown-order@example.com")
    with pytest.raises(service.BillingError):
        service.confirm_checkout(
            db, str(user.id), order_id="order_nope",
            razorpay_payment_id="pay_x", signature="x",
        )


# ---------------------------------------------------------- idempotency


def test_the_same_payment_cannot_buy_two_periods(db, fake_orders):
    """
    The browser and the webhook both report one success. If the second call
    extended the period, every purchase would quietly be worth two months.
    """
    user = _user(db, "double@example.com")
    payment = service.start_checkout(db, str(user.id), PRO)
    payment_id = "pay_once"
    signature = _checkout_signature(payment.razorpay_order_id, payment_id)

    first = service.confirm_checkout(
        db, str(user.id), order_id=payment.razorpay_order_id,
        razorpay_payment_id=payment_id, signature=signature,
    )
    first_end = first.current_period_end

    second = service.confirm_checkout(
        db, str(user.id), order_id=payment.razorpay_order_id,
        razorpay_payment_id=payment_id, signature=signature,
    )

    assert second.current_period_end == first_end
    assert db.query(Subscription).filter(Subscription.user_id == user.id).count() == 1


def test_the_webhook_activates_when_the_browser_never_came_back(db, fake_orders):
    """
    The user paid and closed the laptop. Razorpay's report is what saves them.
    """
    user = _user(db, "closed-laptop@example.com")
    payment = service.start_checkout(db, str(user.id), PRO)

    outcome = service.handle_webhook_event(db, {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": "pay_webhook", "order_id": payment.razorpay_order_id,
        }}},
    })

    assert outcome == "activated"
    db.refresh(user)
    assert user.plan == PRO


def test_a_replayed_webhook_does_not_extend_the_period(db, fake_orders):
    user = _user(db, "replay@example.com")
    payment = service.start_checkout(db, str(user.id), PRO)
    event = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": "pay_replay", "order_id": payment.razorpay_order_id,
        }}},
    }

    service.handle_webhook_event(db, event)
    first_end = db.query(Subscription).filter(Subscription.user_id == user.id).one().current_period_end

    # Razorpay retries for days.
    for _ in range(5):
        service.handle_webhook_event(db, event)

    sub = db.query(Subscription).filter(Subscription.user_id == user.id).one()
    assert sub.current_period_end == first_end


def test_a_late_failure_event_does_not_revoke_a_paid_plan(db, fake_orders):
    """
    A second, abandoned attempt on an already-paid order must not take the
    plan away from someone who paid.
    """
    user = _user(db, "late-failure@example.com")
    payment = service.start_checkout(db, str(user.id), PRO)
    order_id = payment.razorpay_order_id

    service.handle_webhook_event(db, {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_ok", "order_id": order_id}}},
    })
    service.handle_webhook_event(db, {
        "event": "payment.failed",
        "payload": {"payment": {"entity": {
            "id": "pay_later", "order_id": order_id, "error_description": "card declined",
        }}},
    })

    db.refresh(user)
    assert user.plan == PRO
    db.refresh(payment)
    assert payment.status == "paid"


def test_unrecognised_events_are_ignored_not_errors(db, fake_orders):
    """
    A webhook endpoint that raises on an unhandled event gets retried forever
    and is eventually disabled by the provider.
    """
    user = _user(db, "noise@example.com")
    payment = service.start_checkout(db, str(user.id), PRO)

    outcome = service.handle_webhook_event(db, {
        "event": "subscription.charged",
        "payload": {"payment": {"entity": {"id": "p", "order_id": payment.razorpay_order_id}}},
    })
    assert outcome.startswith("ignored")

    assert service.handle_webhook_event(db, {"event": "payment.captured", "payload": {}}) \
        == "ignored: no order id"
    assert service.handle_webhook_event(db, {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "p", "order_id": "order_never_seen"}}},
    }) == "ignored: unknown order"


# ------------------------------------------------- webhook signature HMAC


def test_webhook_signature_verifies_over_the_raw_body():
    body = json.dumps({"event": "payment.captured", "payload": {}}).encode()
    assert razorpay_client.verify_webhook_signature(body, _webhook_signature(body))


def test_webhook_signature_fails_on_a_tampered_body():
    body = json.dumps({"event": "payment.captured"}).encode()
    signature = _webhook_signature(body)
    assert not razorpay_client.verify_webhook_signature(body + b" ", signature)


def test_webhook_signature_is_refused_when_no_secret_is_set(monkeypatch):
    """"No secret configured" must mean reject, never trust."""
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "")
    body = b'{"event":"payment.captured"}'
    assert not razorpay_client.verify_webhook_signature(body, "anything")


def test_an_empty_signature_never_verifies():
    assert not razorpay_client.verify_webhook_signature(b"{}", "")
    assert not razorpay_client.verify_checkout_signature("order_1", "pay_1", "")
    assert not razorpay_client.verify_checkout_signature("", "", "")


# ------------------------------------------------------ cancel and expiry


def test_cancelling_keeps_the_plan_until_the_period_ends(db, fake_orders):
    user = _user(db, "cancel@example.com")
    payment = service.start_checkout(db, str(user.id), PRO)
    payment_id = "pay_cancel"
    service.confirm_checkout(
        db, str(user.id), order_id=payment.razorpay_order_id,
        razorpay_payment_id=payment_id,
        signature=_checkout_signature(payment.razorpay_order_id, payment_id),
    )

    subscription = service.cancel_at_period_end(db, str(user.id))
    assert subscription.cancel_at_period_end is True
    # Still active, still Pro - nothing is taken away now.
    assert subscription.status == "active"
    db.refresh(user)
    assert user.plan == PRO


def test_expiry_puts_the_user_back_on_free(db):
    user = _user(db, "expire@example.com")
    now = datetime.now(timezone.utc)
    db.add(Subscription(
        user_id=user.id, plan=PRO, status="active",
        current_period_start=now - timedelta(days=31),
        current_period_end=now - timedelta(minutes=1),
    ))
    db.query(User).filter(User.id == user.id).update({"plan": PRO})
    db.commit()

    assert service.expire_due_subscriptions(db) == 1

    db.refresh(user)
    assert user.plan == FREE
    assert db.query(Subscription).filter(Subscription.user_id == user.id).one().status == "expired"


def test_expiry_leaves_live_subscriptions_alone(db):
    user = _user(db, "still-live@example.com")
    now = datetime.now(timezone.utc)
    db.add(Subscription(
        user_id=user.id, plan=PRO, status="active",
        current_period_start=now, current_period_end=now + timedelta(days=10),
    ))
    db.query(User).filter(User.id == user.id).update({"plan": PRO})
    db.commit()

    assert service.expire_due_subscriptions(db) == 0
    db.refresh(user)
    assert user.plan == PRO


# -------------------------------------------------------- not configured


def test_checkout_is_refused_when_razorpay_is_not_configured(db, monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    user = _user(db, "unconfigured@example.com")

    with pytest.raises(razorpay_client.RazorpayNotConfigured):
        service.start_checkout(db, str(user.id), PRO)
