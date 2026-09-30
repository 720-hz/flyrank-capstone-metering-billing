"""Idempotency — the heart of the capstone. Probe 1's exact scenario,
run twice: once as a literal double-call, once forcing the UNIQUE
constraint's own race path so the database-level guarantee (not just the
application-level "check then insert") is what's actually being tested."""
import sqlite3

import pytest

from app.lib.errors import PaymentRequiredError, TenantNotFoundError
from app.lib.metering import record_usage
from tests.conftest import make_tenant


def test_same_idempotency_key_records_exactly_one_usage_event(conn):
    tenant_id = make_tenant(conn)

    first = record_usage(conn, tenant_id, "idem-1", input_tokens=100, output_tokens=50)
    second = record_usage(conn, tenant_id, "idem-1", input_tokens=100, output_tokens=50)

    count = conn.execute(
        "SELECT COUNT(*) AS c FROM usage_events WHERE tenant_id=? AND idempotency_key=?", (tenant_id, "idem-1")
    ).fetchone()["c"]
    assert count == 1

    assert first["replayed"] is False
    assert second["replayed"] is True
    # The second response mirrors the first in every field that matters —
    # same cost, same counted usage, same usage_event_id.
    assert second["cost_micro_cents"] == first["cost_micro_cents"]
    assert second["usage_event_id"] == first["usage_event_id"]
    assert second["counted"] == first["counted"]


def test_retry_with_different_body_still_returns_the_original_result(conn):
    """A client that retries after a timeout might not perfectly
    reconstruct the original body — the idempotency key alone is the
    contract; the stored result wins, not whatever the retry happened to
    send. This is what actually prevents a double-charge from a retry
    that (say) double-counted tokens client-side before resending."""
    tenant_id = make_tenant(conn)

    first = record_usage(conn, tenant_id, "idem-2", input_tokens=100)
    second = record_usage(conn, tenant_id, "idem-2", input_tokens=99999)  # different, ignored

    assert second["cost_micro_cents"] == first["cost_micro_cents"]
    count = conn.execute("SELECT COUNT(*) AS c FROM usage_events WHERE tenant_id=?", (tenant_id,)).fetchone()["c"]
    assert count == 1


def test_unique_constraint_is_the_real_race_guard_not_just_the_select(conn):
    """Simulates the TOCTOU race directly: two 'requests' that both pass
    the existence check before either inserts. Only one INSERT may
    succeed — proving the UNIQUE constraint (not applic-level logic) is
    what makes this safe under real concurrency, where two threads truly
    can interleave between the SELECT and the INSERT."""
    tenant_id = make_tenant(conn)
    now = "2026-01-01T00:00:00+00:00"

    conn.execute(
        """INSERT INTO usage_events
           (tenant_id, idempotency_key, input_tokens, cached_input_tokens, output_tokens,
            reasoning_tokens, total_tokens, cost_micro_cents, response_snapshot, created_at)
           VALUES (?, 'race-key', 0,0,0,0,0,0, '{}', ?)""",
        (tenant_id, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO usage_events
               (tenant_id, idempotency_key, input_tokens, cached_input_tokens, output_tokens,
                reasoning_tokens, total_tokens, cost_micro_cents, response_snapshot, created_at)
               VALUES (?, 'race-key', 0,0,0,0,0,0, '{}', ?)""",
            (tenant_id, now),
        )


def test_different_tenants_can_reuse_the_same_idempotency_key(conn):
    """The uniqueness scope is (tenant_id, idempotency_key), not the key
    alone — two different customers each sending 'req-1' as their client's
    own counter must not collide with each other."""
    tenant_a = make_tenant(conn, "tn_a")
    tenant_b = make_tenant(conn, "tn_b")

    a = record_usage(conn, tenant_a, "req-1", input_tokens=10)
    b = record_usage(conn, tenant_b, "req-1", input_tokens=20)

    assert a["replayed"] is False
    assert b["replayed"] is False
    assert a["usage_event_id"] != b["usage_event_id"]


def test_unknown_tenant_raises(conn):
    with pytest.raises(TenantNotFoundError):
        record_usage(conn, "tn_does_not_exist", "idem-x")


def test_inactive_subscription_blocks_with_payment_required(conn):
    tenant_id = make_tenant(conn, plan_id="pro", subscription_status="past_due")
    with pytest.raises(PaymentRequiredError) as exc_info:
        record_usage(conn, tenant_id, "idem-y")
    assert exc_info.value.subscription_status == "past_due"
    # Nothing recorded — a blocked call must not create a billable event.
    count = conn.execute("SELECT COUNT(*) AS c FROM usage_events WHERE tenant_id=?", (tenant_id,)).fetchone()["c"]
    assert count == 0
