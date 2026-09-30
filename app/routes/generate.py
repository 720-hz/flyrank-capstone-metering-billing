"""The one dummy billable endpoint (Section 7). Idempotency-Key is a
required header, not a body field — that's where every real-world
idempotency convention (Stripe's own included) puts it, and it keeps the
key out of the thing being hashed/validated as the request's actual
payload."""
from fastapi import APIRouter, Header, HTTPException

from app.db import db
from app.lib.errors import PaymentRequiredError, QuotaExceededError, TenantNotFoundError
from app.lib.metering import record_usage
from app.schemas import GenerateRequest

router = APIRouter()


@router.post("/v1/generate")
def generate(body: GenerateRequest, idempotency_key: str = Header(..., alias="Idempotency-Key")):
    if not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Idempotency-Key header must not be empty.")

    with db() as conn:
        try:
            result = record_usage(
                conn,
                tenant_id=body.tenant_id,
                idempotency_key=idempotency_key,
                input_tokens=body.input_tokens,
                cached_input_tokens=body.cached_input_tokens,
                output_tokens=body.output_tokens,
                reasoning_tokens=body.reasoning_tokens,
            )
            return result
        except TenantNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except PaymentRequiredError as e:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "payment_required",
                    "message": (
                        f"Tenant's {e.plan_id} plan subscription is '{e.subscription_status}', "
                        "not active. Reactivate the subscription to resume billable actions."
                    ),
                    "plan": e.plan_id,
                    "subscription_status": e.subscription_status,
                },
            ) from e
        except QuotaExceededError as e:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "usage_quota_exceeded",
                    "message": (
                        f"Monthly {e.dimension} quota exceeded: used {e.used}, "
                        f"requested {e.requested}, limit {e.limit}. Resets {e.period_end}."
                    ),
                    "quota": {
                        "dimension": e.dimension,
                        "used": e.used,
                        "requested": e.requested,
                        "limit": e.limit,
                    },
                    "retry_after": e.period_end,
                },
            ) from e
