"""The one dummy billable action's core logic: idempotent recording,
quota enforcement, cost calculation — factored out of the route so
scripts/eval-style tools and tests call exactly the same code path the
live API runs, per the same rule the previous capstone's matching.py
followed.

Idempotency mechanism: `usage_events.UNIQUE(tenant_id, idempotency_key)`
is the actual guard, not an application-level check-then-insert. Two
concurrent requests with the same key can both pass the "does this key
exist?" SELECT before either has inserted (a classic TOCTOU race) — but
only one of their INSERTs can succeed; the database constraint is what's
actually atomic, not the surrounding Python. On IntegrityError we re-read
and return the winner's stored snapshot, same as an ordinary replay.
"""
import json
import sqlite3
from datetime import datetime, timezone

from app.lib.errors import PaymentRequiredError, QuotaExceededError, TenantNotFoundError
from app.lib.money import compute_cost_micro_cents, cost_breakdown
from app.lib.rollup import current_period, usage_rollup


def _now():
    return datetime.now(timezone.utc).isoformat()


def _get_tenant_and_plan(conn, tenant_id: str):
    row = conn.execute(
        """SELECT t.*, p.monthly_api_calls, p.monthly_tokens, p.name AS plan_name
           FROM tenants t JOIN plans p ON p.id = t.plan_id
           WHERE t.id = ?""",
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise TenantNotFoundError(tenant_id)
    return row


def _find_existing(conn, tenant_id: str, idempotency_key: str):
    row = conn.execute(
        "SELECT response_snapshot FROM usage_events WHERE tenant_id = ? AND idempotency_key = ?",
        (tenant_id, idempotency_key),
    ).fetchone()
    return json.loads(row["response_snapshot"]) if row else None


def record_usage(
    conn,
    tenant_id: str,
    idempotency_key: str,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> dict:
    # Step 1: idempotent replay — the second physical request for the same
    # key never reaches quota/cost logic at all.
    existing = _find_existing(conn, tenant_id, idempotency_key)
    if existing is not None:
        return {**existing, "replayed": True}

    tenant = _get_tenant_and_plan(conn, tenant_id)

    # Step 2: payment standing, checked before usage quota — a lapsed
    # subscription blocks every billable action regardless of how much
    # quota room is left.
    if tenant["subscription_status"] != "active":
        raise PaymentRequiredError(tenant["plan_id"], tenant["subscription_status"])

    requested_tokens = input_tokens + cached_input_tokens + output_tokens + reasoning_tokens
    rollup = usage_rollup(conn, tenant_id)

    if rollup["api_calls_used"] + 1 > tenant["monthly_api_calls"]:
        raise QuotaExceededError(
            "api_calls", rollup["api_calls_used"], tenant["monthly_api_calls"], 1, rollup["period_end"]
        )
    if rollup["tokens_used"] + requested_tokens > tenant["monthly_tokens"]:
        raise QuotaExceededError(
            "ai_tokens", rollup["tokens_used"], tenant["monthly_tokens"], requested_tokens, rollup["period_end"]
        )

    # Step 3: allowed — compute cost, insert, snapshot the response.
    cost_micro_cents = compute_cost_micro_cents(input_tokens, cached_input_tokens, output_tokens, reasoning_tokens)
    breakdown = cost_breakdown(input_tokens, cached_input_tokens, output_tokens, reasoning_tokens)
    now = _now()

    response = {
        "tenant_id": tenant_id,
        "idempotency_key": idempotency_key,
        "counted": {"api_calls": 1, "tokens": requested_tokens},
        "cost_micro_cents": cost_micro_cents,
        "cost_breakdown": breakdown,
        "created_at": now,
        "replayed": False,
    }

    try:
        cur = conn.execute(
            """INSERT INTO usage_events
               (tenant_id, idempotency_key, input_tokens, cached_input_tokens, output_tokens,
                reasoning_tokens, total_tokens, cost_micro_cents, response_snapshot, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tenant_id, idempotency_key, input_tokens, cached_input_tokens, output_tokens,
                reasoning_tokens, requested_tokens, cost_micro_cents, json.dumps(response), now,
            ),
        )
        response["usage_event_id"] = cur.lastrowid
    except sqlite3.IntegrityError:
        # Lost the race to a concurrent request with the same key — the
        # UNIQUE constraint is what actually enforced "exactly once" here.
        # Re-read and return the winner's snapshot rather than erroring.
        existing = _find_existing(conn, tenant_id, idempotency_key)
        if existing is not None:
            return {**existing, "replayed": True}
        raise

    # The stored snapshot must match what's returned on replay, including
    # the id — rewrite it now that we know the id.
    conn.execute(
        "UPDATE usage_events SET response_snapshot = ? WHERE id = ?",
        (json.dumps(response), response["usage_event_id"]),
    )
    return response
