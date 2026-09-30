"""Monthly usage rollups — the single source of truth both the quota check
and GET /v1/usage read from, so they can never disagree with each other."""
from datetime import datetime, timezone


def current_period() -> tuple[str, str]:
    """Calendar-month billing period, UTC. Returns (period_start_iso, period_end_exclusive_iso)."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.isoformat(), end.isoformat()


def usage_rollup(conn, tenant_id: str) -> dict:
    period_start, period_end = current_period()
    row = conn.execute(
        """SELECT COUNT(*) AS calls,
                  COALESCE(SUM(total_tokens), 0) AS tokens,
                  COALESCE(SUM(cost_micro_cents), 0) AS cost_micro_cents
           FROM usage_events
           WHERE tenant_id = ? AND created_at >= ? AND created_at < ?""",
        (tenant_id, period_start, period_end),
    ).fetchone()
    return {
        "period_start": period_start,
        "period_end": period_end,
        "api_calls_used": row["calls"],
        "tokens_used": row["tokens"],
        "cost_micro_cents": row["cost_micro_cents"],
    }
