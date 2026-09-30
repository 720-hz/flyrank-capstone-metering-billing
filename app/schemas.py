"""Request-body validation at the boundary — a malformed request never
reaches application logic; it fails here as a clean 422, not a 500 three
layers deeper."""
from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class CheckoutRequest(BaseModel):
    tenant_id: str = Field(min_length=1)


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    email: str | None = None
