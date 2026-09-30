"""Every test gets its own throwaway SQLite file so tests never share
state or race each other — DB_PATH is monkeypatched before app.db is
imported by anything in the test."""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

_tmp_dir = tempfile.mkdtemp(prefix="metering_billing_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")

from app.db import db, init_db  # noqa: E402


@pytest.fixture()
def conn():
    db_path = Path(os.environ["DB_PATH"])
    if db_path.exists():
        db_path.unlink()
    init_db()
    with db() as c:
        yield c


def make_tenant(conn, tenant_id="tn_test", plan_id="free", subscription_status="active"):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO tenants (id, name, email, plan_id, subscription_status, created_at) VALUES (?, 'Test Tenant', 't@example.com', ?, ?, ?)",
        (tenant_id, plan_id, subscription_status, now),
    )
    return tenant_id
