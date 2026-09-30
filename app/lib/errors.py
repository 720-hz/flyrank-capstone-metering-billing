"""Typed errors for the metering path, translated to HTTP responses in
app/routes/generate.py. Carrying structured data (not just a message)
means the route can build the exact { used, limit, requested } body the
brief asks for without re-deriving it."""


class TenantNotFoundError(Exception):
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        super().__init__(f"Unknown tenant: {tenant_id}")


class PaymentRequiredError(Exception):
    """402 — the tenant's subscription itself isn't in good standing.
    Distinct from a usage ceiling: no amount of waiting fixes this, only
    reactivating payment does."""

    def __init__(self, plan_id: str, subscription_status: str):
        self.plan_id = plan_id
        self.subscription_status = subscription_status
        super().__init__(f"Payment required: plan={plan_id} status={subscription_status}")


class QuotaExceededError(Exception):
    """429 — the tenant's plan is in good standing, but this request would
    push usage past the monthly allowance for `dimension`."""

    def __init__(self, dimension: str, used: int, limit: int, requested: int, period_end: str):
        self.dimension = dimension
        self.used = used
        self.limit = limit
        self.requested = requested
        self.period_end = period_end
        super().__init__(
            f"Quota exceeded: {dimension} used={used} requested={requested} limit={limit}"
        )
