"""Token cost estimation with overridable per-model prices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None


@dataclass(frozen=True)
class UsageTotals:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0


# Prices are USD per 1M tokens and intentionally small/sparse. Config overrides
# take precedence; unknown models display tokens only.
BUILTIN_PRICES: dict[str, Price] = {
    "gpt-4o-mini": Price(input=0.15, output=0.60, cache_read=0.075),
    "deepseek-chat": Price(input=0.27, output=1.10, cache_read=0.07),
    # Anthropic Claude 3.5 Sonnet family (prices are USD per 1M tokens).
    "claude-sonnet-4-6": Price(input=3.0, output=15.0, cache_read=0.30, cache_write=3.75),
    "claude-3-5-sonnet": Price(input=3.0, output=15.0, cache_read=0.30, cache_write=3.75),
    "claude-3-5-sonnet-20241022": Price(input=3.0, output=15.0, cache_read=0.30, cache_write=3.75),
    # Kimi / Moonshot AI (USD per 1M tokens; cache hit pricing per official 2026 rates).
    "kimi-k2.7-code": Price(input=0.95, output=4.00, cache_read=0.19),
    "kimi-for-coding": Price(input=0.95, output=4.00, cache_read=0.19),
    "kimi-k2.6": Price(input=0.95, output=4.00, cache_read=0.19),
    "kimi-k2.5": Price(input=0.60, output=3.00),
    "moonshot-v1-128k": Price(input=0.42, output=1.68),
    "moonshot-v1-32k": Price(input=0.21, output=0.84),
    "moonshot-v1-8k": Price(input=0.06, output=0.30),
}


def _override_to_price(raw: object) -> Price | None:
    input_price = getattr(raw, "input", None)
    output_price = getattr(raw, "output", None)
    if input_price is None or output_price is None:
        return None
    return Price(
        input=float(input_price),
        output=float(output_price),
        cache_read=getattr(raw, "cache_read", None),
        cache_write=getattr(raw, "cache_write", None),
    )


def price_for_model(model: str, overrides: Mapping[str, object] | None = None) -> Price | None:
    if overrides and model in overrides:
        override = _override_to_price(overrides[model])
        if override is not None:
            return override
    return BUILTIN_PRICES.get(model)


def estimate_cost_usd(
    totals: UsageTotals,
    *,
    model: str,
    overrides: dict[str, object] | None = None,
) -> float | None:
    price = price_for_model(model, overrides)
    if price is None:
        return None
    cached = max(totals.cached_tokens, 0)
    cache_write = max(totals.cache_write_tokens, 0)
    regular_input = max(totals.prompt_tokens - cached - cache_write, 0)
    cost = regular_input * price.input
    cost += totals.completion_tokens * price.output
    cost += cached * (price.cache_read if price.cache_read is not None else price.input)
    cost += cache_write * (price.cache_write if price.cache_write is not None else price.input)
    return cost / 1_000_000
