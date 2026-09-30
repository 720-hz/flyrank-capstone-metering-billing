"""Idempotent demo-data seed: two tenants, one on each plan, so the README's
"a stranger can run it" setup has something to call against immediately.
Safe to re-run — INSERT OR IGNORE on a fixed id."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import db, init_db  # noqa: E402

DEMO_TENANTS = [
    {"id": "tn_demo_free", "name": "Acme Free Co", "email": "free@example.com", "plan_id": "free"},
    {"id": "tn_demo_pro", "name": "Acme Pro Inc", "email": "pro@example.com", "plan_id": "pro"},
]


def seed_tenants():
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        for t in DEMO_TENANTS:
            conn.execute(
                """INSERT OR IGNORE INTO tenants (id, name, email, plan_id, subscription_status, created_at)
                   VALUES (?, ?, ?, ?, 'active', ?)""",
                (t["id"], t["name"], t["email"], t["plan_id"], now),
            )


def main():
    init_db()
    seed_tenants()
    print(f"Seeded {len(DEMO_TENANTS)} demo tenants: {[t['id'] for t in DEMO_TENANTS]}")


if __name__ == "__main__":
    main()
