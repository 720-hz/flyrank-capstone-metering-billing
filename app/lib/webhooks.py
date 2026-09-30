"""Applying a verified Stripe event to the tenant table. Deduplication
uses the same pattern as idempotent metering (metering.py): a UNIQUE
constraint on webhook_events.stripe_event_id is the actual guard, and the
insert + the tenant update happen inside the SAME `with db()` transaction
(see app/routes/stripe_routes.py) — so a crash partway through can never
leave the event marked "processed" without its side effect actually
applied, or vice versa."""
import json
import sqlite3
from datetime import datetime, timezone

# Stripe subscription statuses that count as "in good standing" — anything
# else blocks billable actions with 402 until it's active again or the
# subscription is deleted (which downgrades the tenant to Free).
ACTIVE_STATUSES = {"active", "trialing"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def process_event(conn, event) -> dict:
    event_id = event["id"]
    event_type = event["type"]
    obj = event["data"]["object"]

    try:
        conn.execute(
            "INSERT INTO webhook_events (stripe_event_id, type, payload, processed_at) VALUES (?, ?, ?, ?)",
            (event_id, event_type, json.dumps(dict(event)), _now()),
        )
    except sqlite3.IntegrityError:
        # Already processed — Stripe redelivers events, and this is the
        # correct response to a replay: acknowledge, touch nothing.
        return {"status": "duplicate", "event_id": event_id, "type": event_type}

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(conn, obj)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(conn, obj)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(conn, obj)
    # Unrecognized event types are acknowledged (200) but otherwise
    # ignored — Stripe accounts emit many event types this app doesn't
    # need to react to; erroring on them would just cause needless retries.

    return {"status": "processed", "event_id": event_id, "type": event_type}


def _tenant_id_for(conn, obj) -> str | None:
    tenant_id = (obj.get("metadata") or {}).get("tenant_id") or obj.get("client_reference_id")
    if tenant_id:
        return tenant_id
    customer_id = obj.get("customer")
    subscription_id = obj.get("id") if obj.get("object") == "subscription" else obj.get("subscription")
    row = None
    if subscription_id:
        row = conn.execute("SELECT id FROM tenants WHERE stripe_subscription_id = ?", (subscription_id,)).fetchone()
    if row is None and customer_id:
        row = conn.execute("SELECT id FROM tenants WHERE stripe_customer_id = ?", (customer_id,)).fetchone()
    return row["id"] if row else None


def _handle_checkout_completed(conn, obj):
    tenant_id = _tenant_id_for(conn, obj)
    if tenant_id is None:
        return  # Nothing this app can attribute the session to.
    conn.execute(
        """UPDATE tenants SET plan_id = 'pro', subscription_status = 'active',
           stripe_customer_id = ?, stripe_subscription_id = ? WHERE id = ?""",
        (obj.get("customer"), obj.get("subscription"), tenant_id),
    )


def _handle_subscription_updated(conn, obj):
    tenant_id = _tenant_id_for(conn, obj)
    if tenant_id is None:
        return
    status = obj.get("status", "unknown")
    conn.execute(
        "UPDATE tenants SET subscription_status = ?, stripe_subscription_id = ? WHERE id = ?",
        (status, obj.get("id"), tenant_id),
    )


def _handle_subscription_deleted(conn, obj):
    tenant_id = _tenant_id_for(conn, obj)
    if tenant_id is None:
        return
    # Truly gone: back to Free, which needs no active payment, so status
    # goes back to 'active' rather than staying in a blocked state forever.
    conn.execute(
        "UPDATE tenants SET plan_id = 'free', subscription_status = 'active' WHERE id = ?",
        (tenant_id,),
    )
