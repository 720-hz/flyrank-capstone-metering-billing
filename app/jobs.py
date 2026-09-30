"""Background jobs. Only one so far: the usage-alert scan (shared
requirement #3 — "at least one background job: slow/bulk work off the
request path, retries + failure alert"). Runs via FastAPI BackgroundTasks
so POST /jobs/scan-usage-alerts returns immediately with a job id, same
pattern as the metering-and-billing capstone's predecessor project.

Same structural rule as that project, carried forward: never call a
function that opens its own `with db()` connection from inside another
already-open `with db()` block — every DB-touching call here is fully
sequential.
"""
from datetime import datetime, timezone

from app.config import ALERT_THRESHOLDS_PCT, MAX_RETRIES
from app.db import db
from app.lib.rollup import usage_rollup


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_job(kind: str, total: int) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO batch_jobs (kind, status, total, processed, failed, started_at) VALUES (?, 'running', ?, 0, 0, ?)",
            (kind, total, _now()),
        )
        return cur.lastrowid


def _finish_job(job_id: int, processed: int, failed: int):
    with db() as conn:
        conn.execute(
            "UPDATE batch_jobs SET status='done', processed=?, failed=?, finished_at=? WHERE id=?",
            (processed, failed, _now(), job_id),
        )


def run_usage_alert_scan(job_id: int):
    """Scans every tenant's current-month rollup; any tenant at or past
    80%/100% of either quota dimension gets a usage_alerts row. A single
    tenant's rollup failing (a bad row, a locked table, whatever) is
    caught and counted as `failed` rather than aborting the whole scan —
    that's the "failure alert" the shared requirement asks for: a failed
    tenant is visible in batch_jobs.failed, not silently dropped."""
    with db() as conn:
        tenants = conn.execute(
            """SELECT t.id, t.plan_id, p.monthly_api_calls, p.monthly_tokens
               FROM tenants t JOIN plans p ON p.id = t.plan_id"""
        ).fetchall()

    processed, failed = 0, 0
    for t in tenants:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with db() as conn:
                    rollup = usage_rollup(conn, t["id"])
                    _write_alerts_if_crossed(
                        conn, t["id"], "api_calls", rollup["api_calls_used"], t["monthly_api_calls"]
                    )
                    _write_alerts_if_crossed(
                        conn, t["id"], "ai_tokens", rollup["tokens_used"], t["monthly_tokens"]
                    )
                processed += 1
                break
            except Exception as e:  # noqa: BLE001 — deliberately broad: any per-tenant failure must not abort the scan
                if attempt == MAX_RETRIES:
                    failed += 1
                    print(f"[usage-alert-scan] giving up on tenant {t['id']} after {attempt} attempts: {e}")

    _finish_job(job_id, processed, failed)


def _write_alerts_if_crossed(conn, tenant_id: str, dimension: str, used: int, limit: int):
    if limit <= 0:
        return
    pct = (used * 100) // limit
    for threshold in ALERT_THRESHOLDS_PCT:
        if pct >= threshold:
            already = conn.execute(
                """SELECT 1 FROM usage_alerts WHERE tenant_id=? AND dimension=? AND threshold_pct=?
                   AND created_at >= date('now', 'start of month')""",
                (tenant_id, dimension, threshold),
            ).fetchone()
            if already is None:
                conn.execute(
                    """INSERT INTO usage_alerts (tenant_id, dimension, threshold_pct, usage_used, usage_limit, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (tenant_id, dimension, threshold, used, limit, _now()),
                )
