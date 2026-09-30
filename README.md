# Usage Metering & Billing Engine

Meters usage, enforces plan quotas, calculates AI-token costs with the real cached-input/reasoning-token pricing rules, and syncs subscription state from Stripe (test mode) through signature-verified, deduplicated webhooks. One dummy billable endpoint exercises every rule.

Full design rationale: [`DESIGN.md`](./DESIGN.md). Proof for every requirement and acceptance probe, with real transcripts: [`EVIDENCE.md`](./EVIDENCE.md). What went into building this, honestly: [`BUILDLOG.md`](./BUILDLOG.md).

## Architecture

```
Client → POST /v1/generate  (Idempotency-Key header, required)
           │
           ▼
     record_usage()  (app/lib/metering.py)
           │
           ├─ 1. lookup (tenant_id, idempotency_key) in usage_events
           │      found → return the STORED response verbatim, "replayed": true
           │      (no new row, no quota re-check, no re-billing)
           │
           ├─ 2. subscription_status != 'active'? → 402 Payment Required
           │
           ├─ 3. current-month rollup + requested usage > plan limit?
           │      → 429 Too Many Requests (names the exact dimension/used/limit)
           │
           └─ 4. compute cost (money.py) → INSERT usage_events
                  (UNIQUE(tenant_id, idempotency_key) is the real concurrency guard)

GET /v1/usage?tenant_id=... ← rollup(usage_events)  → { used, limit, cost }

POST /v1/checkout → Stripe Checkout Session (test mode) → subscription created
Stripe —signed webhook→ POST /webhooks/stripe
           │
           ├─ verify signature against RAW body → forged → 400, nothing touched
           ├─ INSERT webhook_events (stripe_event_id UNIQUE) → duplicate → 200, no-op
           └─ new event → update tenants.plan_id / subscription_status

POST /jobs/scan-usage-alerts (background) → per-tenant rollup → usage_alerts
      rows at 80%/100% of either quota; per-tenant failures retried + counted,
      never abort the whole scan
```

Layering: `app/routes/*.py` is the HTTP layer only (parse request → call a `lib` function → translate the result/exception into a status code). All actual metering, quota, cost, and webhook logic lives in `app/lib/*.py`, called with a plain `sqlite3.Connection` — no route-specific state leaks into it, which is what lets `tests/` exercise the exact same functions the live API runs without spinning up a server.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
```

## Run it

```bash
python scripts/seed.py           # idempotent — creates tn_demo_free (Free) and tn_demo_pro (Pro)
uvicorn app.main:app --reload    # http://localhost:8000
pytest tests/ -v                 # 27 tests, fully offline, ~1s
```

Try the core metering path with no Stripe setup at all:

```bash
curl -X POST http://localhost:8000/v1/generate \
  -H "Content-Type: application/json" -H "Idempotency-Key: try-1" \
  -d '{"tenant_id":"tn_demo_free","input_tokens":1000,"cached_input_tokens":500,"output_tokens":200,"reasoning_tokens":50}'

curl "http://localhost:8000/v1/usage?tenant_id=tn_demo_free"
```

## Stripe setup (test mode — free, no card)

Needed only for `POST /v1/checkout` and `POST /webhooks/stripe`; everything else works with no Stripe account at all.

1. Create a free account at [dashboard.stripe.com](https://dashboard.stripe.com) if you don't have one — no card required, you'll stay in **test mode** the whole time.
2. **API key**: Developers → API keys (test mode) → copy the Secret key (`sk_test_...`) into `.env`'s `STRIPE_SECRET_KEY`.
3. **Pro plan Price**: Product catalog → create a product ("Pro Plan") with a recurring $29.00/month test-mode Price → copy its id (`price_...`) into `.env`'s `STRIPE_PRICE_ID_PRO`. (Or via the [Stripe CLI](https://stripe.com/docs/stripe-cli): `stripe prices create --unit-amount=2900 --currency=usd --recurring[interval]=month -d "product_data[name]=Pro Plan"`.)
4. **Webhook secret**: install the Stripe CLI, then in one terminal:
   ```
   stripe listen --forward-to localhost:8000/webhooks/stripe
   ```
   It prints a `whsec_...` value — put that in `.env`'s `STRIPE_WEBHOOK_SECRET`. Keep this terminal running; it forwards Stripe's real events to your local server, no public URL or tunnel needed.
5. In another terminal, run the app (`uvicorn app.main:app --reload`), then drive a real Checkout:
   ```bash
   curl -X POST http://localhost:8000/v1/checkout -H "Content-Type: application/json" -d '{"tenant_id":"tn_demo_free"}'
   # open the returned checkout_url in a browser, pay with 4242 4242 4242 4242 / any future expiry / any CVC
   ```
   `stripe listen`'s terminal shows the webhook being forwarded; `GET /v1/usage?tenant_id=tn_demo_free` afterward shows `"plan": "pro"` and the new limits.
6. Replay any event without clicking through Checkout again: `stripe trigger checkout.session.completed`.

## Plans

| Plan | API calls / month | AI tokens / month | Price |
|---|---|---|---|
| Free | 1,000 | 100,000 | $0 |
| Pro | 20,000 | 5,000,000 | $29.00/month |

## AI token pricing

```
INPUT_MICROCENTS_PER_TOKEN        = 100   # $1.00 / 1M tokens
CACHED_INPUT_MICROCENTS_PER_TOKEN = 25    # $0.25 / 1M tokens — cheaper
OUTPUT_MICROCENTS_PER_TOKEN       = 400   # $4.00 / 1M tokens
# reasoning tokens billed at the OUTPUT rate — not a separate category
```

Pinned in `app/config.py`. Full math and worked examples: `DESIGN.md` / `EVIDENCE.md`.

## Endpoints

| Endpoint | What it does |
|---|---|
| `POST /v1/tenants` | Create a tenant (starts on Free) |
| `GET /v1/tenants/{id}` | Inspect a tenant |
| `POST /v1/generate` | The one dummy billable action — `Idempotency-Key` header required |
| `GET /v1/usage?tenant_id=` | Monthly rollup: used/limit/cost |
| `POST /v1/checkout` | Real Stripe test-mode Checkout Session for Pro |
| `POST /webhooks/stripe` | Signature-verified, deduplicated webhook receiver |
| `POST /jobs/scan-usage-alerts` | Background job — flags tenants at 80%/100% of quota |
| `GET /jobs/{id}` | Background job status |
| `GET /v1/tenants/{id}/alerts` | A tenant's usage alerts |
| `GET /health` | Liveness |

## Known limitations

- **Calendar-month billing only.** `GET /v1/usage`'s period is the server's own calendar month (UTC) — it doesn't read a tenant's actual Stripe billing-cycle anchor, so a subscription that started mid-month bills on a different cycle than this app assumes. Documented, not silently papered over.
- **No overage billing, invoicing, or proration** — explicitly out of core scope per the brief (Section 7); these are the stretch goals.
- **Simulated AI tokens.** Token counts are supplied by the caller, not produced by calling a real model — that's the brief's own explicit scope (Section 7: "you're metering numbers, not calling a model").

## Sandbox network note

This project was built in a cloud sandbox whose outbound network is limited to package registries — `api.stripe.com` returns a proxy `403` from inside it (confirmed directly; see `BUILDLOG.md`). All core metering/quota/cost/webhook-signature logic is proven with real HTTP calls against the live local server and with an offline automated test suite (`EVIDENCE.md`), none of which need Stripe's actual API. The one thing that does — a real Checkout session completed in a browser — was run on a machine with normal internet access; that transcript is in `EVIDENCE.md`.
