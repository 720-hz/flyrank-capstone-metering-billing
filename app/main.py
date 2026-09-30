from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.db import init_db
from app.routes import checkout_pages, generate, jobs, stripe_routes, tenants, usage

app = FastAPI(title="Usage Metering & Billing Engine")

app.include_router(tenants.router)
app.include_router(generate.router)
app.include_router(usage.router)
app.include_router(stripe_routes.router)
app.include_router(jobs.router)
app.include_router(checkout_pages.router)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.exception_handler(ValidationError)
async def pydantic_validation_handler(request: Request, exc: ValidationError):
    return JSONResponse(status_code=400, content={"error": "Validation failed.", "details": exc.errors()})
