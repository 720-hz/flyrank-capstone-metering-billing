# Build log

Honest AI-usage log, per the brief's rule: "keep BUILDLOG.md honest: where AI helped, where it was wrong, what you changed."

## How this was built

Built with Claude as the pair-programmer, working directly from this brief's Section 6 contract. Design doc first (`DESIGN.md`, Phase 1 gate), then core billing logic (idempotent metering + quota enforcement, Phase 2), then Stripe integration (Phase 3), then cost calculation + finalization (Phase 4) — the order Section 8 lays out, each phase checked against its own gate before moving on.

## A real environment constraint, worked around transparently

The cloud sandbox this was built in has a locked-down network — an organization egress policy that allows only a short list of package registries (npm, PyPI, etc.) and blocks everything else. Confirmed directly rather than assumed:

```
$ curl -sS -o /dev/null -w "HTTP %{http_code}\n" --max-time 8 https://api.stripe.com/v1/charges
curl: (56) CONNECT tunnel failed, response 403
```

This meant:
- **All core logic (idempotent metering, quota boundaries, cost math, webhook signature verification, deduplication) was built and proven with real HTTP calls against the live local server in this sandbox** — none of it needs Stripe's actual API, only `stripe`-shaped data. Webhook signature tests sign payloads locally with the same HMAC-SHA256 scheme Stripe's own docs describe (`tests/stripe_helpers.py`), so verification logic is tested for real, against a real cryptographic signature, without ever calling Stripe.
- **The one thing that genuinely needs Stripe's real API — creating a live Checkout Session and completing it with a test card in a browser (Probe 3) — has to run on a machine with normal internet access.** The application code has no knowledge of this constraint; it just calls the `stripe` SDK normally. It only affected *where* that one specific manual step could physically happen during development.

## Where AI helped

- The idempotency design: using a database `UNIQUE(tenant_id, idempotency_key)` constraint as the actual concurrency guard, rather than an application-level "check if it exists, then insert" — the latter has a real race window between the SELECT and the INSERT that two truly concurrent requests can fall into. `tests/test_metering.py::test_unique_constraint_is_the_real_race_guard_not_just_the_select` exists specifically to prove the constraint is what's doing the work, not the surrounding Python.
- The same pattern applied a second time for webhook deduplication (`webhook_events.stripe_event_id UNIQUE`) — recognizing it was the *same* problem (an at-least-once delivery guarantee needing an exactly-once effect) rather than designing a second, different mechanism.
- The decision to store money in **micro-cents**, not cents. Cents alone lose precision at these per-token rates (a single call can cost a fraction of a cent), and rounding every call to whole cents before summing would let the total drift from the true value over many calls. Working in integer micro-cents throughout, with dollar formatting only ever happening at the very last display step via floor-division/modulo, keeps every step of the actual cost path free of floats or Decimals.
- Separating "quota consumption" (raw token count, for checking against the plan limit) from "priced cost" (the weighted micro-cents total) as two different numbers computed from the same request — conflating them would make a plan's token allowance implicitly depend on the pricing constants, which is the wrong coupling and would make either one harder to change independently later.
- Choosing 402 for "subscription not in good standing" and 429 for "usage ceiling reached," checked in that order (payment standing before quota) — because a lapsed Pro subscription should block *every* billable action immediately, not just the ones that happen to also be over quota.

## Where it was wrong, and what I changed

- **A real test-data bug, caught by the tests themselves, not by inspection.** The first version of `tests/test_quota.py` bulk-inserted synthetic `usage_events` rows dated `2026-01-01` to fast-forward a tenant to its quota boundary. Both boundary tests failed on the first run — not because the quota logic was wrong, but because the *test data* was wrong: the sandbox's real clock is September 2026, and `usage_rollup()`'s current-month query (`WHERE created_at >= period_start AND created_at < period_end`) correctly excluded the January-dated rows from a September rollup, so the tenant never actually reached the fast-forwarded call count the test assumed. The bug was in the test, not the app — caught immediately by running the suite rather than assuming green after writing it. Fixed by generating the synthetic timestamps from `datetime.now()` instead of a hardcoded date, so they always land inside whatever "the current month" actually is when the suite runs. (`git log` / the diff between the first and final `tests/test_quota.py` would show this if this repo tracked that intermediate state — it's reported here instead since the fix landed before the first commit.)

No other functional bug turned up in this build — the idempotency race handling (`try/except sqlite3.IntegrityError` in `record_usage`), the webhook dedup transaction boundary, and the money-formatting logic all passed their tests on the first run. That's a smaller "wrong" section than the previous capstone's, and worth being honest about *why*: this capstone's core logic is small and the patterns (unique-constraint-as-guard, integer-only money math) were deliberately chosen up front specifically because they're hard to get subtly wrong, not because nothing was tried and rejected.

## What I'd explain if asked about any 2-3 lines

- `app/lib/metering.py`'s order of operations — idempotency check, *then* tenant/payment lookup, *then* quota, *then* insert — is deliberate: a replayed request should never even touch the payment-status or quota logic, because it already succeeded once and re-checking could theoretically produce a *different* answer the second time (e.g. quota consumed by other calls in between) for a request that must, by definition, return the same result every time.
- `app/lib/webhooks.py::process_event` inserts the `webhook_events` dedup row and applies the tenant update in the *same* `with db()` transaction (see `app/routes/stripe_routes.py`), not two separate ones — so a crash between "recorded as processed" and "side effect applied" can't happen; either both commit or neither does.
- `_tenant_id_for()` in `webhooks.py` looks up the tenant by `metadata.tenant_id`/`client_reference_id` first (round-tripped through Stripe at Checkout-creation time) and falls back to matching on `stripe_subscription_id`/`stripe_customer_id` for events that don't carry that metadata directly (like `customer.subscription.updated`) — both paths are needed because different Stripe event types carry different fields on their payload object.
- `app/lib/money.py::format_usd()` shows a sub-cent remainder explicitly (`"$0.00 (+212500 sub-cent micro-cents)"`) instead of quietly rounding it away. At these per-token rates a single small call's real cost is genuinely a fraction of a cent; a formatter that just showed `"$0.00"` for a nonzero `cost_micro_cents` would look like a bug (or hide one) even though the stored/summed value is exact — the display makes the sub-cent precision visible rather than pretending it isn't there.
