"""Tests for token cost estimation."""

from mycode.pricing import UsageTotals, estimate_cost_usd, price_for_model


def test_estimate_cost_uses_builtin_model_price() -> None:
    cost = estimate_cost_usd(
        UsageTotals(prompt_tokens=1_000_000, completion_tokens=1_000_000),
        model="gpt-4o-mini",
    )
    assert cost == 0.75


def test_estimate_cost_unknown_model_returns_none() -> None:
    assert estimate_cost_usd(UsageTotals(prompt_tokens=1), model="unknown") is None


def test_estimate_cost_uses_config_override() -> None:
    class Override:
        input = 1.0
        output = 2.0
        cache_read = 0.25
        cache_write = 0.5

    cost = estimate_cost_usd(
        UsageTotals(
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            cached_tokens=100_000,
            cache_write_tokens=100_000,
        ),
        model="custom",
        overrides={"custom": Override()},
    )
    assert cost == 2.875


def test_estimate_cost_uses_claude_builtin_price() -> None:
    cost = estimate_cost_usd(
        UsageTotals(prompt_tokens=1_000_000, completion_tokens=1_000_000),
        model="claude-sonnet-4-6",
    )
    assert cost == 18.0  # 3.0 input + 15.0 output


def test_kimi_models_have_builtin_prices() -> None:
    for model in ("kimi-k2.7-code", "kimi-for-coding", "kimi-k2.6", "kimi-k2.5"):
        assert price_for_model(model) is not None
