"""Read-only usage rollup. No side effects — safe to poll."""
from fastapi import APIRouter, HTTPException, Query

from app.db import db
from app.lib.money import format_usd
from app.lib.rollup import usage_rollup

router = APIRouter()


@router.get("/v1/usage")
def get_usage(tenant_id: str = Query(...)):
    with db() as conn:
        tenant = conn.execute(
            """SELECT t.*, p.name AS plan_name, p.monthly_api_calls, p.monthly_tokens
               FROM tenants t JOIN plans p ON p.id = t.plan_id WHERE t.id = ?""",
            (tenant_id,),
        ).fetchone()
        if tenant is None:
            raise HTTPException(status_code=404, detail=f"Unknown tenant: {tenant_id}")

        rollup = usage_rollup(conn, tenant_id)

    return {
        "tenant_id": tenant_id,
        "plan": tenant["plan_id"],
        "subscription_status": tenant["subscription_status"],
        "period": {"start": rollup["period_start"], "end": rollup["period_end"]},
        "api_calls": {"used": rollup["api_calls_used"], "limit": tenant["monthly_api_calls"]},
        "ai_tokens": {"used": rollup["tokens_used"], "limit": tenant["monthly_tokens"]},
        "cost_micro_cents": rollup["cost_micro_cents"],
        "cost_usd": format_usd(rollup["cost_micro_cents"]),
    }
