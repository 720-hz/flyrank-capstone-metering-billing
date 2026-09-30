"""Builds a real, correctly-signed Stripe webhook header locally — same
HMAC-SHA256 scheme Stripe's own docs describe — so webhook signature
verification can be tested deterministically with no network call and no
real Stripe account. See Stripe's "Verify webhook signatures" docs for
the scheme this replicates."""
import hashlib
import hmac
import time


def sign_payload(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"
