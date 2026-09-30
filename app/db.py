"""SQLite connection + schema. One connection per `with db()` block, always
closed before the next one opens — see app/jobs.py's module docstring for
why that rule exists (a lesson from the previous capstone, carried forward
on purpose)."""
import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH, PLANS

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    monthly_api_calls INTEGER NOT NULL,
    monthly_tokens INTEGER NOT NULL,
    price_cents INTEGER NOT NULL,
    stripe_price_id TEXT
);

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    plan_id TEXT NOT NULL DEFAULT 'free' REFERENCES plans(id),
    stripe_customer_id TEXT UNIQUE,
    stripe_subscription_id TEXT UNIQUE,
    subscription_status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tenants_stripe_customer ON tenants(stripe_customer_id);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    idempotency_key TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cost_micro_cents INTEGER NOT NULL DEFAULT 0,
    response_snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_usage_tenant_created ON usage_events(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stripe_event_id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    dimension TEXT NOT NULL,
    threshold_pct INTEGER NOT NULL,
    usage_used INTEGER NOT NULL,
    usage_limit INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_tenant ON usage_alerts(tenant_id);

CREATE TABLE IF NOT EXISTS batch_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    total INTEGER NOT NULL DEFAULT 0,
    processed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
"""


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)
        for plan_id, p in PLANS.items():
            conn.execute(
                """INSERT INTO plans (id, name, monthly_api_calls, monthly_tokens, price_cents, stripe_price_id)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, monthly_api_calls=excluded.monthly_api_calls,
                     monthly_tokens=excluded.monthly_tokens, price_cents=excluded.price_cents,
                     stripe_price_id=excluded.stripe_price_id""",
                (plan_id, p["name"], p["monthly_api_calls"], p["monthly_tokens"], p["price_cents"], p["stripe_price_id"]),
            )
