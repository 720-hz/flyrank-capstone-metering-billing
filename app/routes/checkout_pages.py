"""Plain-text landing targets for Stripe Checkout's success_url/cancel_url
(see app/lib/stripe_client.py). No frontend in scope for this capstone —
these exist only so a real browser completing a real test-mode Checkout
doesn't land on a 404; the actual plan change is driven entirely by the
webhook, not by the browser reaching this page."""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.get("/checkout/success")
def checkout_success(session_id: str | None = None):
    return PlainTextResponse(
        "Checkout completed. Stripe will deliver a checkout.session.completed webhook shortly — "
        f"that event (not this page) is what actually flips the tenant's plan. session_id={session_id}"
    )


@router.get("/checkout/cancel")
def checkout_cancel():
    return PlainTextResponse("Checkout canceled — no subscription was created, no plan change happened.")
