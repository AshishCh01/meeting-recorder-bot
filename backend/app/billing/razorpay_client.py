"""
The Razorpay half of billing - billing Phase 3.

Deliberately not the `razorpay` SDK. The whole integration is one REST call
(create an order), one optional read (fetch a payment) and two HMAC checks,
and httpx is already a dependency of this service while the SDK would pull in
`requests` - a second HTTP stack in the production image for about forty lines
of work. Using httpx directly also means the tests stub one transport instead
of monkeypatching somebody else's client object.

**This runs on test keys.** Razorpay's test mode takes no KYC, accepts only
its own test cards (4111 1111 1111 1111, any future expiry and CVV) and moves
no real money. Nothing here is ready for live keys: there is no retry on a
failed capture, no dunning, no proration and no refund path. See
app/billing/__init__.py.

Two rules the rest of billing depends on:

- **The amount is computed here from the plan catalogue, never taken from the
  client.** A browser that could name its own price would buy Team for one
  rupee. The API takes a plan id and a seat count; charge_paise() turns that
  into money.

- **Every signature check is constant-time** (hmac.compare_digest), for the
  same reason verify_webhook_token in api/auth.py is: an early-exit `==` on a
  secret leaks it a byte at a time to anyone who can measure the response.
"""
import hashlib
import hmac
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://api.razorpay.com/v1"

# Razorpay is a payment provider, not a model: a slow response means a user
# staring at a checkout button, so this is short and the failure is surfaced
# rather than retried. Creating an order twice would be worse than failing
# once - the second order is a second chance to be charged.
TIMEOUT_SECONDS = 15.0


class RazorpayNotConfigured(RuntimeError):
    """No usable key pair. The API turns this into a 503, never a 500."""


class RazorpayError(RuntimeError):
    """Razorpay refused or could not be reached. Carries a safe-to-show message."""


def _auth() -> tuple:
    """
    HTTP Basic, which is how Razorpay authenticates the REST API: the key id
    is the username and the key secret is the password.
    """
    if not settings.billing_enabled:
        raise RazorpayNotConfigured(
            "Razorpay is not configured - set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
        )
    return (settings.razorpay_key_id, settings.razorpay_key_secret)


def create_order(amount_paise: int, receipt: str, notes: Optional[dict] = None) -> dict:
    """
    Opens a Razorpay order and returns its entity, whose `id` (order_xxx) is
    the idempotency key for everything that follows - it is what
    payments.razorpay_order_id stores under a unique constraint.

    `amount_paise` must already be the catalogue price; this function does not
    look one up, so that there is exactly one place (plans.charge_paise) that
    decides what something costs.
    """
    if amount_paise <= 0:
        # A zero-amount order is not a free plan, it is a bug - Free is the
        # absence of a subscription and never reaches checkout.
        raise ValueError("amount_paise must be positive, got {}".format(amount_paise))

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        # Our own reference, echoed back on the order. Razorpay caps it at 40
        # characters and rejects anything longer, so callers pass a short one.
        "receipt": receipt[:40],
        # 1 = capture automatically on success. The alternative is authorise
        # now and capture later, which needs a capture call, a timeout policy
        # and a reconciliation job - none of which exist here.
        "payment_capture": 1,
        "notes": notes or {},
    }

    try:
        response = httpx.post(
            "{}/orders".format(API_ROOT),
            json=payload,
            auth=_auth(),
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        logger.warning("[billing] could not reach Razorpay to create an order: %s", e)
        raise RazorpayError("Could not reach Razorpay. Please try again.")

    if response.status_code >= 400:
        # Razorpay's own description is safe to log but not to show: it can
        # name account-level configuration. The user gets a generic sentence.
        logger.error(
            "[billing] Razorpay refused an order (HTTP %s): %s",
            response.status_code, response.text[:500],
        )
        raise RazorpayError("Razorpay could not start this payment. Please try again.")

    order = response.json()
    logger.info(
        "[billing] opened Razorpay order %s for %s paise", order.get("id"), amount_paise
    )
    return order


def fetch_payment(payment_id: str) -> dict:
    """
    Reads a payment back from Razorpay - the authoritative answer to "was this
    actually captured", as opposed to what a browser told us.

    Used by the webhook path as a cross-check. Raises RazorpayError rather
    than returning a partial dict, so a caller cannot mistake a failed read
    for an uncaptured payment.
    """
    try:
        response = httpx.get(
            "{}/payments/{}".format(API_ROOT, payment_id),
            auth=_auth(),
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        logger.warning("[billing] could not fetch payment %s: %s", payment_id, e)
        raise RazorpayError("Could not reach Razorpay.")

    if response.status_code >= 400:
        logger.error(
            "[billing] Razorpay refused a payment read (HTTP %s): %s",
            response.status_code, response.text[:500],
        )
        raise RazorpayError("Could not read this payment from Razorpay.")

    return response.json()


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Checks the HMAC that Razorpay Checkout hands back to the browser.

    The signed message is exactly "<order_id>|<payment_id>", keyed with the
    API secret. This is what stops a browser from POSTing a made-up payment id
    and being given a Pro plan: only someone holding the secret can produce a
    matching digest, and the secret never leaves this service.

    Returns False rather than raising on anything malformed - a missing or
    wrong-shaped signature is a failed verification, not an error condition.
    """
    if not (order_id and payment_id and signature):
        return False
    if not settings.razorpay_key_secret:
        logger.error("[billing] cannot verify a checkout signature with no key secret set")
        return False

    expected = hmac.new(
        settings.razorpay_key_secret.encode("utf-8"),
        "{}|{}".format(order_id, payment_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    Checks the X-Razorpay-Signature header on an incoming webhook.

    Keyed with RAZORPAY_WEBHOOK_SECRET - a different secret from the API key,
    set per endpoint in the Razorpay dashboard - over the raw request body.
    Raw bytes, not a re-serialised dict: re-encoding JSON reorders keys and
    changes whitespace, and the digest would never match.
    """
    if not signature:
        return False
    if not settings.razorpay_webhook_secret:
        # Refusing is the safe default. An unverified webhook can grant plans,
        # so "no secret configured" must mean "reject", never "trust".
        logger.error("[billing] a webhook arrived but RAZORPAY_WEBHOOK_SECRET is not set - rejecting")
        return False

    expected = hmac.new(
        settings.razorpay_webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)
