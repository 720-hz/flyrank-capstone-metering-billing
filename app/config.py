"""Central config, loaded once from .env. Nothing secret ever has a default
that looks like a real credential — an unset STRIPE_SECRET_KEY fails loudly
the first time it's actually needed, not silently."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID_PRO = os.environ.get("STRIPE_PRICE_ID_PRO", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "billing.db"))
PORT = int(os.environ.get("PORT", "8000"))

# --- Plans -------------------------------------------------------------
# See DESIGN.md "Plans & quotas" for why these numbers were picked.
PLANS = {
    "free": {
        "name": "Free",
        "monthly_api_calls": 1_000,
        "monthly_tokens": 100_000,
        "price_cents": 0,
        "stripe_price_id": None,
    },
    "pro": {
        "name": "Pro",
        "monthly_api_calls": 20_000,
        "monthly_tokens": 5_000_000,
        "price_cents": 2_900,
        "stripe_price_id": STRIPE_PRICE_ID_PRO,
    },
}

# --- AI token pricing (pinned; see DESIGN.md "AI token pricing rules") -
# All in MICRO-CENTS per token (1 cent = 1,000,000 micro-cents) so
# cost = tokens * rate is exact integer multiplication, never a float.
INPUT_MICROCENTS_PER_TOKEN = 100  # $1.00 / 1M tokens
CACHED_INPUT_MICROCENTS_PER_TOKEN = 25  # $0.25 / 1M tokens — cached input is cheaper
OUTPUT_MICROCENTS_PER_TOKEN = 400  # $4.00 / 1M tokens
# Reasoning tokens are billed at the OUTPUT rate — they are not a separate,
# cheaper category, and they are never added to the input side.

# Usage-alert thresholds (background job — see DESIGN.md).
ALERT_THRESHOLDS_PCT = [80, 100]

MAX_RETRIES = 3
