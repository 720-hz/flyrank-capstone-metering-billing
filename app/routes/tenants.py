"""Tenant CRUD — minimal, just enough to create demo tenants and inspect
one. No cross-tenant listing endpoint on purpose: nothing here should make
it easy to accidentally build a UI that leaks tenant B's data into tenant
A's view."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db import db
from app.schemas import TenantCreateRequest

router = APIRouter()


@router.post("/v1/tenants", status_code=201)
def create_tenant(body: TenantCreateRequest):
    tenant_id = f"tn_{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute(
            "INSERT INTO tenants (id, name, email, plan_id, subscription_status, created_at) VALUES (?, ?, ?, 'free', 'active', ?)",
            (tenant_id, body.name, body.email, now),
        )
    return {"id": tenant_id, "name": body.name, "email": body.email, "plan_id": "free", "subscription_status": "active"}


@router.get("/v1/tenants/{tenant_id}")
def get_tenant(tenant_id: str):
    with db() as conn:
        row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown tenant: {tenant_id}")
    return dict(row)
