"""All direct Stripe SDK calls live here, nowhere else — so the rest of
the app talks to this module's functions, not to `stripe.*` directly, and
mocking/testing the webhook-handling logic never needs a real network
call (see tests/test_webhooks.py)."""
import stripe

from app.config import APP_BASE_URL, STRIPE_PRICE_ID_PRO, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET

stripe.api_key = STRIPE_SECRET_KEY


class WebhookVerificationError(Exception):
    pass


def create_checkout_session(tenant_id: str, tenant_email: str | None, stripe_customer_id: str | None) -> dict:
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY is not set — cannot create a real Checkout session.")
    if not STRIPE_PRICE_ID_PRO:
        raise RuntimeError("STRIPE_PRICE_ID_PRO is not set — create a test-mode Price for the Pro plan first (see README).")

    kwargs = {
        "mode": "subscription",
        "line_items": [{"price": STRIPE_PRICE_ID_PRO, "quantity": 1}],
        "success_url": f"{APP_BASE_URL}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{APP_BASE_URL}/checkout/cancel",
        # Round-trips our tenant id through Stripe so the webhook handler
        # can attribute the event without a separate lookup table.
        "client_reference_id": tenant_id,
        "metadata": {"tenant_id": tenant_id},
    }
    if stripe_customer_id:
        kwargs["customer"] = stripe_customer_id
    elif tenant_email:
        kwargs["customer_email"] = tenant_email

    session = stripe.checkout.Session.create(**kwargs)
    return {"checkout_url": session.url, "session_id": session.id}


def verify_and_parse_event(payload: bytes, sig_header: str):
    """Verifies the signature against the RAW request body — not the
    parsed/re-serialized JSON, which would change byte-for-byte and break
    verification. Raises WebhookVerificationError on any failure (bad
    signature, malformed payload, wrong secret)."""
    if not STRIPE_WEBHOOK_SECRET:
        raise WebhookVerificationError("STRIPE_WEBHOOK_SECRET is not set.")
    try:
        return stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        raise WebhookVerificationError(str(e)) from e
