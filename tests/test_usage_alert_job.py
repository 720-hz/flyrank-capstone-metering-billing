"""The background job (shared requirement #3). Runs the scan function
directly (not through BackgroundTasks/HTTP) so it's deterministic and
synchronous in the test, while still being the exact function the API
schedules."""
from app.jobs import _write_alerts_if_crossed
from tests.conftest import make_tenant


def test_alert_written_once_per_threshold_per_month(conn):
    tenant_id = make_tenant(conn)

    _write_alerts_if_crossed(conn, tenant_id, "api_calls", used=850, limit=1000)  # 85% -> crosses 80
    _write_alerts_if_crossed(conn, tenant_id, "api_calls", used=850, limit=1000)  # same scan run again

    rows = conn.execute("SELECT * FROM usage_alerts WHERE tenant_id=?", (tenant_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["threshold_pct"] == 80


def test_both_thresholds_fire_at_100_percent(conn):
    tenant_id = make_tenant(conn)
    _write_alerts_if_crossed(conn, tenant_id, "ai_tokens", used=100_000, limit=100_000)  # exactly 100%

    rows = conn.execute(
        "SELECT threshold_pct FROM usage_alerts WHERE tenant_id=? ORDER BY threshold_pct", (tenant_id,)
    ).fetchall()
    assert [r["threshold_pct"] for r in rows] == [80, 100]


def test_no_alert_below_threshold(conn):
    tenant_id = make_tenant(conn)
    _write_alerts_if_crossed(conn, tenant_id, "api_calls", used=100, limit=1000)  # 10%

    rows = conn.execute("SELECT * FROM usage_alerts WHERE tenant_id=?", (tenant_id,)).fetchall()
    assert len(rows) == 0
