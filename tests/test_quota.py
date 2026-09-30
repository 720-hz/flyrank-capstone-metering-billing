"""Boundary honesty — Probe 2's exact scenario. Free plan's 1,000 API
calls/month is used directly (not mocked smaller) so this test proves the
same number documented in DESIGN.md and README."""
from datetime import datetime, timezone

import pytest

from app.lib.errors import QuotaExceededError
from app.lib.metering import record_usage
from tests.conftest import make_tenant

_NOW = datetime.now(timezone.utc).isoformat()  # must fall inside the rollup's current-month window


def _bulk_insert_calls(conn, tenant_id, count, start_at=2):
    """Fast path to pre-fill usage without 1,000 Python-level calls to
    record_usage — each row is still a real, distinct, valid usage_event."""
    rows = [
        (tenant_id, f"bulk-{i}", 0, 0, 0, 0, 0, 0, "{}", _NOW)
        for i in range(start_at, start_at + count)
    ]
    conn.executemany(
        """INSERT INTO usage_events
           (tenant_id, idempotency_key, input_tokens, cached_input_tokens, output_tokens,
            reasoning_tokens, total_tokens, cost_micro_cents, response_snapshot, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )


def test_call_exactly_at_the_limit_is_allowed(conn):
    tenant_id = make_tenant(conn, plan_id="free")
    record_usage(conn, tenant_id, "call-1")  # call #1
    _bulk_insert_calls(conn, tenant_id, count=998, start_at=2)  # calls #2..#999

    result = record_usage(conn, tenant_id, "call-1000")  # call #1000 — exactly at the 1,000 limit
    assert result["replayed"] is False

    count = conn.execute("SELECT COUNT(*) AS c FROM usage_events WHERE tenant_id=?", (tenant_id,)).fetchone()["c"]
    assert count == 1000


def test_call_one_past_the_limit_is_rejected_with_exact_numbers(conn):
    tenant_id = make_tenant(conn, plan_id="free")
    _bulk_insert_calls(conn, tenant_id, count=1000, start_at=1)  # calls #1..#1000, at the limit

    with pytest.raises(QuotaExceededError) as exc_info:
        record_usage(conn, tenant_id, "call-1001")  # call #1001 — one past the limit

    err = exc_info.value
    assert err.dimension == "api_calls"
    assert err.used == 1000
    assert err.limit == 1000
    assert err.requested == 1

    # Rejected — nothing recorded for the rejected call.
    count = conn.execute("SELECT COUNT(*) AS c FROM usage_events WHERE tenant_id=?", (tenant_id,)).fetchone()["c"]
    assert count == 1000


def test_token_quota_boundary_is_exact(conn):
    tenant_id = make_tenant(conn, plan_id="free")  # 100,000 tokens/month
    # 99,999 tokens used so far via one bulk row.
    conn.execute(
        """INSERT INTO usage_events
           (tenant_id, idempotency_key, input_tokens, cached_input_tokens, output_tokens,
            reasoning_tokens, total_tokens, cost_micro_cents, response_snapshot, created_at)
           VALUES (?, 'pre', 99999,0,0,0,99999,0,'{}',?)""",
        (tenant_id, _NOW),
    )

    # Requesting exactly 1 more token lands exactly at the 100,000 limit — allowed.
    result = record_usage(conn, tenant_id, "token-boundary-ok", input_tokens=1)
    assert result["replayed"] is False

    # The next request, even for 1 token, is now over the limit — rejected.
    with pytest.raises(QuotaExceededError) as exc_info:
        record_usage(conn, tenant_id, "token-boundary-over", input_tokens=1)
    assert exc_info.value.dimension == "ai_tokens"
    assert exc_info.value.used == 100_000
    assert exc_info.value.limit == 100_000


def test_pro_plan_has_higher_limits_than_free(conn):
    from app.config import PLANS

    assert PLANS["pro"]["monthly_api_calls"] > PLANS["free"]["monthly_api_calls"]
    assert PLANS["pro"]["monthly_tokens"] > PLANS["free"]["monthly_tokens"]
