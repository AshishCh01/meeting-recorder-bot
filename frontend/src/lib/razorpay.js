/**
 * Razorpay Checkout, loaded on demand.
 *
 * The script is not in index.html because almost nobody who opens MeetIQ is
 * about to pay: loading a third-party payment SDK on every page view costs
 * every visitor a request for something one in a hundred will use. It is
 * fetched the first time someone presses an upgrade button, and the promise
 * is cached so a second press does not fetch it again.
 *
 * **The browser is never trusted with what was paid.** Checkout hands back a
 * signed order/payment pair, which goes straight to POST /billing/verify; the
 * backend recomputes the HMAC and only then grants anything. Nothing here
 * decides a plan - this file opens a dialog and reports what it returned.
 */
const CHECKOUT_SRC = 'https://checkout.razorpay.com/v1/checkout.js';

let loader = null;

export function loadRazorpay() {
  if (window.Razorpay) return Promise.resolve(window.Razorpay);
  if (loader) return loader;

  loader = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = CHECKOUT_SRC;
    script.async = true;
    script.onload = () => resolve(window.Razorpay);
    script.onerror = () => {
      // Cleared so a later attempt can retry rather than being stuck with a
      // rejected promise forever - this fails on flaky networks and on ad
      // blockers, both of which the user can fix and try again.
      loader = null;
      reject(new Error('Could not load Razorpay Checkout.'));
    };
    document.body.appendChild(script);
  });

  return loader;
}

/**
 * Opens Checkout for an order from POST /billing/orders.
 *
 * `onSuccess` receives exactly the three signed fields POST /billing/verify
 * expects. `onDismiss` fires when the dialog is closed without paying, which
 * is not an error - the order is simply abandoned and stays "created", and
 * billing/service.py never shows those in the history.
 */
export async function openCheckout({ order, email, planName, onSuccess, onDismiss, onError }) {
  const Razorpay = await loadRazorpay();

  const checkout = new Razorpay({
    key: order.key_id,
    amount: order.amount_paise,
    currency: order.currency,
    order_id: order.order_id,
    name: 'MeetIQ',
    description: `${planName || order.plan} plan`,
    // Saves the user retyping what we already know. Not a security boundary -
    // the backend attributes the order by its own record, not by this.
    prefill: email ? { email } : undefined,
    theme: { color: '#2563eb' },
    handler: (response) => {
      onSuccess?.({
        razorpay_order_id: response.razorpay_order_id,
        razorpay_payment_id: response.razorpay_payment_id,
        razorpay_signature: response.razorpay_signature,
      });
    },
    modal: {
      ondismiss: () => onDismiss?.(),
    },
  });

  // A payment that Razorpay itself rejects (card declined, 3DS failed). The
  // dialog stays open for a retry, so this reports rather than closes.
  checkout.on('payment.failed', (event) => {
    onError?.(event?.error?.description || 'That payment did not go through.');
  });

  checkout.open();
  return checkout;
}
