# Design — Usage Metering & Billing Engine

One page, written before Phase 2, per the brief's Phase 1 gate.

## Scope (Section 7)

2 plans (Free / Pro) · 2 usage types (API calls, AI tokens) · 1 dummy billable endpoint (`POST /v1/generate`). No invoicing, proration, or overage billing in core — those stay stretch goals. AI tokens are simulated: the caller reports token counts in the request body, nothing calls a real model.

## Plans & quotas

| Plan | API calls / month | AI tokens / month | Price |
|---|---|---|---|
| Free | 1,000 | 100,000 | $0 |
| Pro | 20,000 | 5,000,000 | $29.00/month |

Pro's numbers are a deliberate ~20x step up from Free — generous enough that a demo tenant on Pro won't accidentally hit quota during testing, while Free's limits are small enough to walk to the boundary in a handful of calls.

## Money math — integer micro-cents, no floats anywhere

The brief's rule: store money as integers, never floats. Cents alone aren't precise enough here — at realistic per-token rates a single call can cost a fraction of a cent, and summing rounded per-call cents across thousands of calls would drift from the true total. So the base unit is **micro-cents** (1 cent = 1,000,000 micro-cents; 1 USD = 100,000,000 micro-cents). Every pricing constant is an integer number of micro-cents per token, so `cost = tokens * rate` is exact integer multiplication — no `Decimal`, no rounding, no floating point, anywhere in the cost path. A dollar amount is only ever produced at the very last step, for display, via integer floor-division and modulo (`micro_cents // 100_000_000` and the remainder), never fed back into further arithmetic.

## AI token pricing rules (pinned in `app/config.py`)

```
INPUT_MICROCENTS_PER_TOKEN          = 100   # $1.00 / 1M tokens
CACHED_INPUT_MICROCENTS_PER_TOKEN   = 25    # $0.25 / 1M tokens — cached input is cheaper
OUTPUT_MICROCENTS_PER_TOKEN         = 400   # $4.00 / 1M tokens
# reasoning tokens are billed at the OUTPUT rate, not a separate category
```

```
cost_micro_cents = input_tokens        * INPUT_MICROCENTS_PER_TOKEN
                  + cached_input_tokens * CACHED_INPUT_MICROCENTS_PER_TOKEN
                  + (output_tokens + reasoning_tokens) * OUTPUT_MICROCENTS_PER_TOKEN
```

Token categories are never just added together and priced at one rate — that's the mistake this capstone exists to catch. `EVIDENCE.md` has worked totals for specific inputs.

API-call usage has no per-call price in this scope (it's covered by the flat plan price) — Pro's $29/month is a subscription fee, not a per-call charge. Per-call overage pricing for API calls is a stretch goal (Section 9), not core.

Quota consumption for the **tokens** dimension is measured in raw token count (`input + cached_input + output + reasoning`, unweighted) against the plan's monthly token allowance — a separate number from the *priced* total above. Quota asks "how much volume," cost asks "how much money"; conflating them would mean a plan's token limit implicitly depends on the pricing constants, which is the wrong coupling.

## The metering API contract & idempotency strategy

**`POST /v1/generate`** — the one dummy billable endpoint. Every call is exactly one billable action: it always counts as 1 API call, and it may also report AI token usage.

Request:
```
Headers: Idempotency-Key: <opaque client-generated string>
Body: {
  "tenant_id": "...",
  "input_tokens": 0, "cached_input_tokens": 0,
  "output_tokens": 0, "reasoning_tokens": 0
}
```

Idempotency is enforced with a database constraint, not an in-memory cache: `usage_events` has `UNIQUE(tenant_id, idempotency_key)`. The full response body computed on first success is stored alongside the row (`response_snapshot`). Flow:

1. Look up `(tenant_id, idempotency_key)` in `usage_events`.
2. **Found** → return the stored `response_snapshot` verbatim, `200`, `"replayed": true` added. No new row, no quota re-check, no re-billing. This is what makes a network-level retry safe: the *second* physical request never reaches the quota/cost logic at all.
3. **Not found** → check quota (below) → on pass, insert the row (the `UNIQUE` constraint is the actual concurrency guard against two simultaneous racing requests with the same key — a duplicate insert fails at the database level, not by a race-prone read-then-write in application code) → compute cost → build the response → store it as the snapshot → return it.

Quota check, before insert:
```
requested_calls  = 1
requested_tokens = input_tokens + cached_input_tokens + output_tokens + reasoning_tokens
used             = rollup of this tenant's usage_events for the current calendar month
```
- If `tenant.subscription_status` is not active (Pro tenant whose Stripe subscription lapsed) → **402 Payment Required**, nothing recorded. This is the "you must pay to continue" case — distinct from a usage ceiling.
- Else if `used.calls + requested_calls > plan.monthly_api_calls` OR `used.tokens + requested_tokens > plan.monthly_tokens` → **429 Too Many Requests**, nothing recorded, response names which dimension and the exact numbers (`used`, `limit`, `requested`).
- Else → allowed, proceed to step 3 above.

A request that would land **exactly on** the limit (`used + requested == limit`) is allowed — the boundary itself is inside the plan, only the call that would push usage *past* it is rejected. Documented and tested explicitly (Probe 2 — see `EVIDENCE.md`).

**`GET /v1/usage?tenant_id=...`** — rollup: `{ plan, period, api_calls: {used, limit}, ai_tokens: {used, limit}, cost_micro_cents, cost_usd }`. Read path only, no side effects.

## Stripe integration (test mode)

**`POST /v1/checkout`** — creates a Stripe Checkout Session for the tenant to subscribe to Pro; returns the session URL. No card details ever touch this backend — Stripe's hosted page handles that.

**`POST /webhooks/stripe`** — receives `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`.
1. Verify the signature (`Stripe-Signature` header against `STRIPE_WEBHOOK_SECRET`) using the *raw* request body — not the parsed/re-serialized JSON, which would break verification. A bad signature → `400`, nothing touched.
2. Deduplicate by Stripe's own event id: `webhook_events.stripe_event_id UNIQUE`. Insert first; a unique-constraint failure means this exact event was already processed, so return `200` immediately without reapplying any side effect. Same database-constraint-as-guard pattern as the idempotency key above, not an in-memory set.
3. On a new event, update `tenants.plan_id` / `subscription_status` / `stripe_subscription_id` from the event payload. Stripe is the source of truth for payment state; this database only mirrors it through verified, deduplicated events — never written from anywhere else.

## Database schema

```
plans
  id (free|pro), name, monthly_api_calls, monthly_tokens,
  price_cents, stripe_price_id

tenants
  id, name, email, plan_id -> plans.id (default 'free'),
  stripe_customer_id (nullable, unique), stripe_subscription_id (nullable, unique),
  subscription_status (active|past_due|canceled|none, default 'active'),
  created_at
  INDEX(stripe_customer_id)

usage_events
  id, tenant_id -> tenants.id, idempotency_key,
  input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,
  total_tokens (= sum of the 4, for quota),
  cost_micro_cents (priced total, weighted — see pricing rules),
  response_snapshot (JSON, the exact response returned on first success),
  created_at
  UNIQUE(tenant_id, idempotency_key)
  INDEX(tenant_id, created_at)   -- monthly rollups scan this

webhook_events
  id, stripe_event_id (unique), type, payload (JSON), processed_at
  UNIQUE(stripe_event_id)

usage_alerts
  id, tenant_id -> tenants.id, dimension (api_calls|ai_tokens),
  threshold_pct (80|100), usage_used, usage_limit, created_at
  -- written by the background usage-alert-scan job, never by request handlers

batch_jobs
  id, kind, status (running|done|failed), total, processed, failed,
  started_at, finished_at
```

Every table with a `tenant_id` is scoped to it in every query (`WHERE tenant_id = ?`) — there's no cross-tenant read path in this codebase, which is what "customer data isolated per tenant" means in practice, not just in principle.

## Background job (shared requirement #3)

**Usage-alert scan** (`POST /jobs/scan-usage-alerts`, kicked off via FastAPI `BackgroundTasks`, same pattern as the batch-job design from the prior capstone): scans every tenant's current-month rollup, and for any tenant at ≥80% or ≥100% of either quota dimension, writes a `usage_alerts` row. Per-tenant failures are caught and logged individually (one bad tenant doesn't abort the scan), retried up to 3 times, and the job's own `batch_jobs` row records `processed`/`failed` counts — the "failure alert" the shared requirement asks for is that a failed tenant shows up in that count rather than silently vanishing. This is real background work off the request path (nothing in `POST /v1/generate` blocks on it), not a disguised synchronous call.

## One explicit non-goal

**No invoicing, proration, or overage billing.** Section 7 marks these as stretch goals with real teeth, not core scope. `POST /v1/generate` blocking cleanly at the quota boundary with a clear 429/402 is the core promise; letting a tenant go over and billing them for the excess is a deliberately separate, harder problem this build doesn't claim to solve.
