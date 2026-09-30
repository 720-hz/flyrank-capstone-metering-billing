"""Money math — integer micro-cents only. See DESIGN.md "Money math" for
why the base unit is micro-cents rather than cents: at these per-token
rates a single call often costs a fraction of a cent, and rounding each
call to whole cents before summing would drift from the true total over
many calls. `format_usd` is the ONLY place a human-readable dollar string
is produced, and it never uses float or Decimal — just integer
floor-division and modulo, so nothing downstream can round-trip a rounded
value back into further arithmetic by mistake.
"""
from app.config import (
    CACHED_INPUT_MICROCENTS_PER_TOKEN,
    INPUT_MICROCENTS_PER_TOKEN,
    OUTPUT_MICROCENTS_PER_TOKEN,
)

MICRO_CENTS_PER_USD = 100_000_000  # 100 cents/dollar * 1,000,000 micro-cents/cent


def compute_cost_micro_cents(
    input_tokens: int, cached_input_tokens: int, output_tokens: int, reasoning_tokens: int
) -> int:
    """Reasoning tokens are billed at the OUTPUT rate — never a separate
    category, never added to input. Categories are never summed and then
    priced at one blended rate; each is priced at its own rate first."""
    return (
        input_tokens * INPUT_MICROCENTS_PER_TOKEN
        + cached_input_tokens * CACHED_INPUT_MICROCENTS_PER_TOKEN
        + (output_tokens + reasoning_tokens) * OUTPUT_MICROCENTS_PER_TOKEN
    )


def cost_breakdown(
    input_tokens: int, cached_input_tokens: int, output_tokens: int, reasoning_tokens: int
) -> dict:
    input_cost = input_tokens * INPUT_MICROCENTS_PER_TOKEN
    cached_cost = cached_input_tokens * CACHED_INPUT_MICROCENTS_PER_TOKEN
    output_cost = (output_tokens + reasoning_tokens) * OUTPUT_MICROCENTS_PER_TOKEN
    return {
        "input_micro_cents": input_cost,
        "cached_input_micro_cents": cached_cost,
        "output_and_reasoning_micro_cents": output_cost,
        "total_micro_cents": input_cost + cached_cost + output_cost,
    }


def format_usd(micro_cents: int) -> str:
    """Integer-only formatting: floor-division and modulo, no float/Decimal."""
    sign = "-" if micro_cents < 0 else ""
    m = abs(micro_cents)
    dollars = m // MICRO_CENTS_PER_USD
    remainder_micro = m % MICRO_CENTS_PER_USD
    cents = remainder_micro // 1_000_000
    # Sub-cent remainder, shown so small per-call costs aren't silently
    # rounded away in the display string (still not used in any further math).
    sub_cent_micro = remainder_micro % 1_000_000
    if sub_cent_micro:
        return f"{sign}${dollars}.{cents:02d} (+{sub_cent_micro} sub-cent micro-cents)"
    return f"{sign}${dollars}.{cents:02d}"
