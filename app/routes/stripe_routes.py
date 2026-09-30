"""Checkout session creation + the webhook receiver. The webhook route
reads the RAW body (`await request.body()`), not a parsed model — Stripe
signs the exact bytes it sent, and re-serializing parsed JSON before
verifying would almost always produce a different byte string and fail
verification for reasons that have nothing to do with a real forgery."""
from fastapi import APIRouter, Header, HTTPException, Request

from app.db import db
from app.lib.stripe_client import WebhookVerificationError, create_checkout_session, verify_and_parse_event
from app.lib.webhooks import process_event
from app.schemas import CheckoutRequest

router = APIRouter()


@router.post("/v1/checkout")
def checkout(body: CheckoutRequest):
    with db() as conn:
        tenant = conn.execute("SELECT * FROM tenants WHERE id = ?", (body.tenant_id,)).fetchone()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Unknown tenant: {body.tenant_id}")

    try:
        result = create_checkout_session(tenant["id"], tenant["email"], tenant["stripe_customer_id"])
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: str = Header(default="", alias="Stripe-Signature")):
    payload = await request.body()
    try:
        event = verify_and_parse_event(payload, stripe_signature)
    except WebhookVerificationError as e:
        # Forged or malformed — nothing is touched, and the 400 itself is
        # the proof (Probe 4).
        raise HTTPException(status_code=400, detail=f"Webhook signature verification failed: {e}") from e

    with db() as conn:
        result = process_event(conn, event)
    return result
