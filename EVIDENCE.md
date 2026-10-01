# Evidence

One proof per requirement checkbox (Section 6) and one per acceptance probe (Section 12). Everything below except Probe 3 was captured against the live app running in this environment (`uvicorn app.main:app`), via real HTTP requests over `curl` — not a reimplementation, not mocked at the route level. Probe 3 needs a real Stripe account and a real browser completing Stripe's hosted Checkout page, which this build environment's network can't reach — see that section for the real transcript, captured on a machine with normal internet access.

## Metering — idempotent, no double-counting (Probe 1)

Two physically separate HTTP requests, identical `Idempotency-Key`, against the live server:

```
--- request 1 ---
{"tenant_id":"tn_demo_free","idempotency_key":"demo-key-001","counted":{"api_calls":1,"tokens":2900},
 "cost_micro_cents":372500,"cost_breakdown":{"input_micro_cents":200000,"cached_input_micro_cents":12500,
 "output_and_reasoning_micro_cents":160000,"total_micro_cents":372500},
 "created_at":"2026-09-30T15:45:17.134223+00:00","replayed":false,"usage_event_id":1}

--- request 2 (identical Idempotency-Key, retried) ---
{"tenant_id":"tn_demo_free","idempotency_key":"demo-key-001","counted":{"api_calls":1,"tokens":2900},
 "cost_micro_cents":372500,"cost_breakdown":{"input_micro_cents":200000,"cached_input_micro_cents":12500,
 "output_and_reasoning_micro_cents":160000,"total_micro_cents":372500},
 "created_at":"2026-09-30T15:45:17.134223+00:00","replayed":true,"usage_event_id":1}

--- usage_events row count for this key ---
usage_events rows for demo-key-001: 1
```

The second response mirrors the first exactly (same `usage_event_id`, same cost, same `created_at`) except for `"replayed": true`, and exactly one row exists in `usage_events` for that key. Also covered by `tests/test_metering.py` (6 tests, including a direct test of the `UNIQUE(tenant_id, idempotency_key)` constraint under a simulated race — see "Automated tests" below).

## Quota enforcement — exact boundary (Probe 2)

Free plan: 1,000 API calls/month. A tenant fast-forwarded to exactly 999 recorded calls, then two more calls sent for real against the live server:

```
--- call #1000 (== limit, must succeed) ---
HTTP 200

--- call #1001 (== limit+1, must reject) ---
{"detail":{"error":"usage_quota_exceeded","message":"Monthly api_calls quota exceeded: used 1000, requested 1,
 limit 1000. Resets 2026-10-01T00:00:00+00:00.","quota":{"dimension":"api_calls","used":1000,"requested":1,
 "limit":1000},"retry_after":"2026-10-01T00:00:00+00:00"}}
HTTP 429
```

The call that lands exactly on the limit succeeds; the next one is rejected with a `429` naming the exact dimension, used/requested/limit numbers, and the reset time. The token dimension's boundary is checked the identical way in `tests/test_quota.py::test_token_quota_boundary_is_exact`.

### 402 vs 429 — documented rule

```
--- Pro tenant's subscription set to 'past_due', then a billable call ---
{"detail":{"error":"payment_required","message":"Tenant's pro plan subscription is 'past_due', not active.
 Reactivate the subscription to resume billable actions.","plan":"pro","subscription_status":"past_due"}}
HTTP 402
```

**Rule:** `429` means "your plan is fine, you've used up this month's allowance" (a usage ceiling — waiting until next month, or upgrading, both fix it). `402` means "your plan itself isn't paid for right now" (a Pro tenant whose Stripe subscription lapsed — no amount of waiting fixes it, only reactivating payment does). The two are checked in that order in `app/lib/metering.py::record_usage`: payment standing first, usage quota second — a lapsed subscription blocks every billable action regardless of how much quota room is technically left.

## Cost calculation — pinned pricing rules (Probe 5)

Pricing constants (`app/config.py`): input $1.00/1M tokens, cached input $0.25/1M tokens (cheaper), output **and reasoning** $4.00/1M tokens (reasoning is billed at the output rate, not a separate category, and never added to input). All arithmetic is integer micro-cents — no float or Decimal anywhere in the cost path (see DESIGN.md "Money math").

Worked example, `input=1000, cached_input=500, output=200, reasoning=50` (the exact call in Probe 1's transcript used `input=2000, cached_input=500, output=300, reasoning=100`, shown at that scale below the totals):

```
input_micro_cents               = 1000 * 100                = 100,000
cached_input_micro_cents        =  500 * 25                  =  12,500
output_and_reasoning_micro_cents= (200 + 50) * 400            = 100,000
total_micro_cents                                            = 212,500
```

`tests/test_money.py` (7 tests) proves this against the actual `compute_cost_micro_cents`/`cost_breakdown` functions, including a test that a naive "sum all tokens then price at one rate" implementation gives a *different, wrong* number than pricing each category separately (`test_categories_are_not_blended_into_one_rate`) — the exact mistake this capstone exists to catch:

```
tests/test_money.py::test_pure_input_tokens PASSED
tests/test_money.py::test_cached_input_is_cheaper_than_fresh_input PASSED
tests/test_money.py::test_reasoning_tokens_priced_as_output_not_a_separate_category PASSED
tests/test_money.py::test_categories_are_not_blended_into_one_rate PASSED
tests/test_money.py::test_worked_example_matches_breakdown PASSED
tests/test_money.py::test_format_usd_is_integer_only_and_round_trips_a_whole_dollar PASSED
tests/test_money.py::test_format_usd_shows_sub_cent_remainder_rather_than_silently_rounding PASSED
```

`GET /v1/usage` sums `cost_micro_cents` straight from the same `usage_events` rows `/v1/generate` writes — there's no separate cost-recomputation path that could drift from what was actually billed per call.

## Webhook signature verification + deduplication (Probe 4)

A locally HMAC-signed payload (same scheme Stripe's own docs describe — see `tests/stripe_helpers.py`), sent to the live `/webhooks/stripe` endpoint three times: once forged, then a real event delivered twice (simulating Stripe's own at-least-once redelivery guarantee):

```
--- forged signature -> must be 400, tenant untouched ---
{"detail":"Webhook signature verification failed: No signatures found matching the expected signature for payload"}
HTTP 400

--- valid signature, delivery #1 -> processed, tenant flips free->pro ---
{"status":"processed","event_id":"evt_demo_checkout_1","type":"checkout.session.completed"}
HTTP 200

--- valid signature, delivery #2 (Stripe redelivery of the SAME event) -> must be 'duplicate', not reapplied ---
{"status":"duplicate","event_id":"evt_demo_checkout_1","type":"checkout.session.completed"}
HTTP 200

--- tenant state after all three deliveries ---
{"id":"tn_demo_free","plan_id":"pro","stripe_customer_id":"cus_demo_1","stripe_subscription_id":"sub_demo_1",
 "subscription_status":"active", ...}
--- webhook_events row count for this event id (must be 1) ---
webhook_events rows for evt_demo_checkout_1: 1
```

The forgery is rejected before touching the database at all (`400`, signature checked against the raw request body). The real event is applied exactly once — `webhook_events.stripe_event_id UNIQUE` is what actually enforces that, verified directly in `tests/test_webhooks.py` by asserting on row counts, not just on the HTTP response.

## Stripe Checkout end-to-end (Probe 3) — real test-mode run

This is the one probe that genuinely needs a real Stripe test-mode account, a real browser completing Stripe's own hosted Checkout page with the test card `4242 4242 4242 4242`, and `stripe listen` forwarding a real webhook — none of which this build environment's network can reach (outbound access here is limited to package registries; `api.stripe.com` returns a proxy `403` — confirmed directly, see `BUILDLOG.md`). Captured on a machine with normal internet access, real Stripe test-mode sandbox account:

**1. Checkout session created** (`POST /v1/checkout`, `tenant_id: tn_demo_free`, which starts on Free):
```
Stripe API Version [2026-08-26.dahlia]
session_id = cs_test_a1MDnHpMY10kuWD24F1e9pNnssxz11KJRXFFgXgXPeCcAPZsIlavWHrodG
```
Completed in a real browser at Stripe's hosted Checkout page with test card `4242 4242 4242 4242`.

**2. `stripe listen` forwarded the real webhooks** (not simulated — Stripe's own servers, in response to the real Checkout completing):
```
Ready! Your webhook signing secret is whsec_580efdf3384782b864e19d16ed472caf0870b55d3c505e09841f8aa01661e4e3

2026-09-30 19:25:54   --> checkout.session.completed [evt_1ULQKLLz2NZzXX8jrh7B4hRC]
2026-09-30 19:25:54  <--  [200] POST http://localhost:8000/webhooks/stripe [evt_1ULQKLLz2NZzXX8jrh7B4hRC]
2026-09-30 19:25:55   --> customer.subscription.updated [evt_1ULQKMLz2NZzXX8j5Pi1B7GS]
2026-09-30 19:25:55  <--  [200] POST http://localhost:8000/webhooks/stripe [evt_1ULQKMLz2NZzXX8j5Pi1B7GS]
```
Both real Stripe events verified (real signature, not a locally-signed test payload this time) and processed with a real `200`.

**3. `GET /v1/usage?tenant_id=tn_demo_free`, after the webhooks landed:**
```
tenant_id           : tn_demo_free
plan                : pro
subscription_status : active
period              : @{start=2026-09-01T00:00:00+00:00; end=2026-10-01T00:00:00+00:00}
api_calls           : @{used=0; limit=20000}
ai_tokens           : @{used=0; limit=5000000}
cost_micro_cents    : 0
cost_usd            : $0.00
```
`plan: pro` and the limits (20,000 calls / 5,000,000 tokens) confirm the tenant was genuinely flipped from Free to Pro by the real webhook, not by any direct database write — nothing in this app's code sets `plan_id` outside `app/lib/webhooks.py`'s event handlers.

## Automated tests — one command, deterministic, no network

```
pytest tests/ -v
```

27 tests, 0 network calls (webhook signature tests sign/verify locally with a test-only secret — see `tests/stripe_helpers.py` — never touching Stripe's real API), each test gets its own throwaway SQLite file (`tests/conftest.py`):

```
tests/test_metering.py::test_same_idempotency_key_records_exactly_one_usage_event PASSED
tests/test_metering.py::test_retry_with_different_body_still_returns_the_original_result PASSED
tests/test_metering.py::test_unique_constraint_is_the_real_race_guard_not_just_the_select PASSED
tests/test_metering.py::test_different_tenants_can_reuse_the_same_idempotency_key PASSED
tests/test_metering.py::test_unknown_tenant_raises PASSED
tests/test_metering.py::test_inactive_subscription_blocks_with_payment_required PASSED
tests/test_money.py (7 tests) PASSED
tests/test_quota.py (4 tests) PASSED
tests/test_usage_alert_job.py (3 tests) PASSED
tests/test_webhooks.py (7 tests) PASSED

27 passed in 0.96s
```

## Shared requirements

| # | Requirement | Where |
|---|---|---|
| 1 | Layered architecture | `app/routes` (HTTP) → `app/lib` (logic) → `app/db.py` (data). Routes never touch SQL directly except trivial lookups; all metering/quota/cost/webhook logic lives in `app/lib`. |
| 2 | Validation at the boundary | `app/schemas.py` (Pydantic) rejects malformed bodies as `422` before any handler runs; the global `ValidationError` handler in `app/main.py` catches anything raised deeper as a clean `400`, never a `500`. |
| 3 | ≥1 background job | `POST /jobs/scan-usage-alerts` (`app/jobs.py`) — off the request path via `BackgroundTasks`, per-tenant retries (`MAX_RETRIES`), failures counted in `batch_jobs.failed` rather than aborting the scan. Tested in `tests/test_usage_alert_job.py`. |
| 4 | Real persistence | SQLite with an explicit schema (`app/db.py`), indexes on `(tenant_id, created_at)` for rollups and on `stripe_customer_id`, every query scoped to `WHERE tenant_id = ?`. |
| 5 | Idempotency where it matters | `usage_events.UNIQUE(tenant_id, idempotency_key)` and `webhook_events.UNIQUE(stripe_event_id)` — both enforced at the database level, not just checked in application code. |
| 6 | Secrets clean | `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` only ever read from `.env` (gitignored); never logged (webhook payloads are logged to `webhook_events.payload`, but never the secret itself or the raw signature header). |
| 7 | Cost tracked (AI usage) | Every `usage_events` row stores its own `cost_micro_cents` and full `cost_breakdown` at write time — not recomputed later — and `GET /v1/usage` sums directly from those rows. |

## Known limitation

`GET /v1/usage`'s period is the current calendar month, computed from the server's own clock (UTC) — there's no support for a tenant on a non-calendar billing cycle (e.g. "signed up on the 15th, bills the 15th–14th"). Real Stripe subscriptions do support custom billing anchors; this build's usage rollup doesn't read that from Stripe and assumes calendar-month billing throughout. Documented rather than silently assumed.
