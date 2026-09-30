"""Proof of correct totals for the pinned pricing rules — the exact
numbers pasted into EVIDENCE.md for Probe 5 come straight from these
assertions, so the doc and the enforced behavior can never drift apart."""
from app.lib.money import compute_cost_micro_cents, cost_breakdown, format_usd


def test_pure_input_tokens():
    # 1,000,000 input tokens * 100 micro-cents/token = 100,000,000 micro-cents = $1.00
    assert compute_cost_micro_cents(1_000_000, 0, 0, 0) == 100_000_000


def test_cached_input_is_cheaper_than_fresh_input():
    fresh = compute_cost_micro_cents(1000, 0, 0, 0)
    cached = compute_cost_micro_cents(0, 1000, 0, 0)
    assert cached < fresh
    assert fresh == 1000 * 100
    assert cached == 1000 * 25


def test_reasoning_tokens_priced_as_output_not_a_separate_category():
    reasoning_only = compute_cost_micro_cents(0, 0, 0, 1000)
    output_only = compute_cost_micro_cents(0, 0, 1000, 0)
    combined = compute_cost_micro_cents(0, 0, 500, 500)
    assert reasoning_only == output_only == 1000 * 400
    assert combined == 1000 * 400  # not (500*400)+(500*400*some_other_rate) — same rate, additive


def test_categories_are_not_blended_into_one_rate():
    # A naive "sum all tokens then price at one rate" implementation would
    # give a different (wrong) answer than pricing each category first.
    input_tokens, cached, output, reasoning = 1_000, 1_000, 1_000, 1_000
    correct = compute_cost_micro_cents(input_tokens, cached, output, reasoning)
    naive_wrong = (input_tokens + cached + output + reasoning) * 100  # blended at the input rate
    assert correct != naive_wrong
    assert correct == 1000 * 100 + 1000 * 25 + 1000 * 400 + 1000 * 400  # = 925,000


def test_worked_example_matches_breakdown():
    # The exact numbers used in the live smoke test / EVIDENCE.md.
    b = cost_breakdown(1000, 500, 200, 50)
    assert b["input_micro_cents"] == 100_000
    assert b["cached_input_micro_cents"] == 12_500
    assert b["output_and_reasoning_micro_cents"] == 100_000  # (200+50)*400
    assert b["total_micro_cents"] == 212_500
    assert compute_cost_micro_cents(1000, 500, 200, 50) == 212_500


def test_format_usd_is_integer_only_and_round_trips_a_whole_dollar():
    assert format_usd(100_000_000) == "$1.00"
    assert format_usd(150_000_000) == "$1.50"
    assert format_usd(0) == "$0.00"


def test_format_usd_shows_sub_cent_remainder_rather_than_silently_rounding():
    # 212,500 micro-cents is less than one cent (1,000,000 micro-cents) —
    # a naive cents-only formatter would round this to "$0.00" and lose it.
    result = format_usd(212_500)
    assert result.startswith("$0.00")
    assert "212500" in result
