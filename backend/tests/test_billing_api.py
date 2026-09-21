"""
The billing endpoints as a browser meets them - Phase 3.

test_billing_checkout.py covers the service logic. This file covers the HTTP
surface: that the routes are mounted, that secrets do not leak into responses,
and - the one that actually matters - that POST /billing/webhook refuses an
unsigned request. That endpoint can grant a paid plan while holding no user
session, so its signature check is the entire security boundary.
"""
import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import auth
from app.billing import razorpay_client
from app.billing.plans import FREE, PRO, TEAM
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Payment, Subscription, User
from app.main import app

HEADERS = {"Authorization": "Bearer stub-token-identity-is-stubbed"}
KEY_ID = "rzp_test_stubkey"
KEY_SECRET = "stub-secret-not-a-real-one"
WEBHOOK_SECRET = "stub-webhook-secret"


@pytest.fixture(autouse=True)
def razorpay_keys(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", KEY_ID)
    monkeypatch.setattr(settings, "razorpay_key_secret", KEY_SECRET)
    monkeypatch.setattr(settings, "razorpay_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)


@pytest.fixture
def signed_in(monkeypatch):
    user_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(User(id=user_id, email="bill-{}@example.com".format(user_id)))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(
        auth, "_identify", lambda token: (str(user_id), "bill-{}@example.com".format(user_id))
    )
    auth.user_row_cache.clear()
    yield str(user_id)
    auth.user_row_cache.clear()


@pytest.fixture
def fake_orders(monkeypatch):
    class FakeResponse:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def fake_post(url, json=None, auth=None, timeout=None):
        return FakeResponse({
            "id": "order_{}".format(uuid.uuid4().hex[:14]),
            "amount": json["amount"],
            "currency": json["currency"],
            "receipt": json["receipt"],
            "status": "created",
        })

    monkeypatch.setattr(razorpay_client.httpx, "post", fake_post)


def _checkout_signature(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(
        secret.encode(), "{}|{}".format(order_id, payment_id).encode(), hashlib.sha256
    ).hexdigest()


# ------------------------------------------------------------ catalogue


def test_plans_are_public():
    """The pricing page is public, and a plan holds nothing secret."""
    response = TestClient(app).get("/billing/plans")
    assert response.status_code == 200

    plans = response.json()
    assert [p["id"] for p in plans] == [FREE, PRO, TEAM]
    assert [p["price_rupees"] for p in plans] == [0, 500, 1200]
    # Unlimited must travel as null, so the UI never renders it as 0.
    team = plans[2]
    assert team["meetings_per_period"] is None
    assert team["ask_ai_questions_per_period"] is None


def test_plans_carry_the_duration_a_recording_actually_gets(monkeypatch):
    """
    Phase 5: the pricing page prints effective_max_duration_minutes. Pro is
    designed for 180, but a deployment whose recorder stops at 90 must not
    advertise three hours - that is a pricing page stating something untrue.
    """
    monkeypatch.setattr(settings, "max_recording_duration_minutes", 90)
    plans = TestClient(app).get("/billing/plans").json()
    by_id = {p["id"]: p for p in plans}

    assert by_id[PRO]["max_duration_minutes"] == 180      # what the tier is for
    assert by_id[PRO]["effective_max_duration_minutes"] == 90   # what it gets here
    # Free's own cap is lower than the recorder's, so it binds instead.
    assert by_id[FREE]["effective_max_duration_minutes"] == 45

    monkeypatch.setattr(settings, "max_recording_duration_minutes", 240)
    plans = TestClient(app).get("/billing/plans").json()
    assert {p["id"]: p for p in plans}[PRO]["effective_max_duration_minutes"] == 180


def test_billing_state_starts_on_free(signed_in):
    response = TestClient(app).get("/billing", headers=HEADERS)
    assert response.status_code == 200

    body = response.json()
    assert body["current_plan"] == FREE
    assert body["subscription"] is None
    assert body["usage"]["meetings_limit"] == 5
    assert body["usage"]["ai_questions_limit"] == 20
    assert body["billing_enabled"] is True


def test_billing_state_needs_a_session():
    assert TestClient(app).get("/billing").status_code in (401, 403)


# ------------------------------------------------------------- checkout


def test_an_order_returns_only_the_public_key(signed_in, fake_orders):
    response = TestClient(app).post("/billing/orders", json={"plan": PRO}, headers=HEADERS)
    assert response.status_code == 200

    body = response.json()
    assert body["amount_paise"] == 50_000
    assert body["key_id"] == KEY_ID
    assert body["test_mode"] is True
    # The secret must never reach the browser, by any field name.
    assert KEY_SECRET not in json.dumps(body)
    assert WEBHOOK_SECRET not in json.dumps(body)


def test_the_client_cannot_name_its_own_price(signed_in, fake_orders):
    """
    An amount in the request body is ignored - the catalogue decides. Without
    this, Team costs whatever the browser says it costs.
    """
    response = TestClient(app).post(
        "/billing/orders",
        json={"plan": TEAM, "seats": 1, "amount_paise": 100, "price_rupees": 1},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["amount_paise"] == 120_000


def test_buying_free_is_a_400(signed_in, fake_orders):
    response = TestClient(app).post("/billing/orders", json={"plan": FREE}, headers=HEADERS)
    assert response.status_code == 400


def test_an_unconfigured_razorpay_is_a_503_not_a_500(signed_in, monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    response = TestClient(app).post("/billing/orders", json={"plan": PRO}, headers=HEADERS)
    assert response.status_code == 503


def test_verify_grants_the_plan_and_returns_the_new_state(signed_in, fake_orders):
    client = TestClient(app)
    order = client.post("/billing/orders", json={"plan": PRO}, headers=HEADERS).json()
    payment_id = "pay_api"

    response = client.post(
        "/billing/verify",
        json={
            "razorpay_order_id": order["order_id"],
            "razorpay_payment_id": payment_id,
            "razorpay_signature": _checkout_signature(order["order_id"], payment_id),
        },
        headers=HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["current_plan"] == PRO
    assert body["subscription"]["plan"] == PRO
    assert body["subscription"]["status"] == "active"
    # And the quota lifted with it.
    assert body["usage"]["meetings_limit"] == 30
    assert body["usage"]["ai_questions_limit"] is None


def test_verify_with_a_bad_signature_is_a_400_and_grants_nothing(signed_in, fake_orders):
    client = TestClient(app)
    order = client.post("/billing/orders", json={"plan": PRO}, headers=HEADERS).json()

    response = client.post(
        "/billing/verify",
        json={
            "razorpay_order_id": order["order_id"],
            "razorpay_payment_id": "pay_forged",
            "razorpay_signature": "00" * 32,
        },
        headers=HEADERS,
    )

    assert response.status_code == 400
    assert client.get("/billing", headers=HEADERS).json()["current_plan"] == FREE


# -------------------------------------------------------------- webhook


def _post_webhook(client, event, secret=WEBHOOK_SECRET, signature=None):
    raw = json.dumps(event).encode()
    if signature is None:
        signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/billing/webhook",
        content=raw,
        headers={"X-Razorpay-Signature": signature, "Content-Type": "application/json"},
    )


def test_an_unsigned_webhook_is_refused(signed_in, fake_orders):
    """
    The whole boundary. This endpoint grants paid plans and holds no user
    session - an unverified request must be rejected, never trusted.
    """
    client = TestClient(app)
    order = client.post("/billing/orders", json={"plan": TEAM}, headers=HEADERS).json()
    event = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_x", "order_id": order["order_id"]}}},
    }

    refused = client.post("/billing/webhook", content=json.dumps(event).encode())
    assert refused.status_code == 401

    forged = _post_webhook(client, event, signature="ff" * 32)
    assert forged.status_code == 401

    wrong_secret = _post_webhook(client, event, secret="not-the-webhook-secret")
    assert wrong_secret.status_code == 401

    # Nothing was granted by any of the three.
    assert client.get("/billing", headers=HEADERS).json()["current_plan"] == FREE


def test_a_signed_webhook_activates_the_plan(signed_in, fake_orders):
    client = TestClient(app)
    order = client.post("/billing/orders", json={"plan": PRO}, headers=HEADERS).json()

    response = _post_webhook(client, {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_hook", "order_id": order["order_id"]}}},
    })

    assert response.status_code == 200
    assert response.json()["outcome"] == "activated"
    assert client.get("/billing", headers=HEADERS).json()["current_plan"] == PRO


def test_an_unhandled_but_signed_event_is_a_200(signed_in, fake_orders):
    """A 500 here would be retried forever and eventually disable the hook."""
    client = TestClient(app)
    response = _post_webhook(client, {"event": "refund.created", "payload": {}})
    assert response.status_code == 200
    assert response.json()["outcome"].startswith("ignored")


def test_a_signed_body_that_is_not_json_is_a_400(signed_in):
    client = TestClient(app)
    raw = b"this is not json"
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    response = client.post(
        "/billing/webhook", content=raw, headers={"X-Razorpay-Signature": signature}
    )
    assert response.status_code == 400


# -------------------------------------------------- history and cancel


def test_history_hides_abandoned_checkouts(signed_in, fake_orders):
    client = TestClient(app)
    # One abandoned, one paid.
    client.post("/billing/orders", json={"plan": PRO}, headers=HEADERS)
    order = client.post("/billing/orders", json={"plan": PRO}, headers=HEADERS).json()
    payment_id = "pay_history"
    client.post(
        "/billing/verify",
        json={
            "razorpay_order_id": order["order_id"],
            "razorpay_payment_id": payment_id,
            "razorpay_signature": _checkout_signature(order["order_id"], payment_id),
        },
        headers=HEADERS,
    )

    history = client.get("/billing/payments", headers=HEADERS).json()
    assert len(history) == 1
    assert history[0]["status"] == "paid"
    assert history[0]["amount_paise"] == 50_000


def test_cancel_keeps_the_plan_until_the_period_ends(signed_in, fake_orders):
    client = TestClient(app)
    order = client.post("/billing/orders", json={"plan": PRO}, headers=HEADERS).json()
    payment_id = "pay_cancel_api"
    client.post(
        "/billing/verify",
        json={
            "razorpay_order_id": order["order_id"],
            "razorpay_payment_id": payment_id,
            "razorpay_signature": _checkout_signature(order["order_id"], payment_id),
        },
        headers=HEADERS,
    )

    response = client.post("/billing/cancel", headers=HEADERS)
    assert response.status_code == 200

    body = response.json()
    assert body["subscription"]["cancel_at_period_end"] is True
    assert body["current_plan"] == PRO  # nothing taken away now


def test_cancelling_without_a_subscription_is_a_400(signed_in):
    assert TestClient(app).post("/billing/cancel", headers=HEADERS).status_code == 400
