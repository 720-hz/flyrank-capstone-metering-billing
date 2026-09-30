from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.db import db
from app.jobs import create_job, run_usage_alert_scan

router = APIRouter()


@router.post("/jobs/scan-usage-alerts", status_code=202)
def scan_usage_alerts(background_tasks: BackgroundTasks):
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM tenants").fetchone()["c"]
    job_id = create_job("usage_alert_scan", total)
    background_tasks.add_task(run_usage_alert_scan, job_id)
    return {"job_id": job_id, "status": "running", "total": total}


@router.get("/jobs/{job_id}")
def get_job(job_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM batch_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return dict(row)


@router.get("/v1/tenants/{tenant_id}/alerts")
def list_alerts(tenant_id: str):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM usage_alerts WHERE tenant_id = ? ORDER BY created_at DESC", (tenant_id,)
        ).fetchall()
    return [dict(r) for r in rows]
