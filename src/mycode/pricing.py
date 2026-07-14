"""Token cost estimation with overridable per-model prices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mycode.catalog import MODEL_CATALOG_DATA


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


# Prices are USD per 1M tokens. The versioned catalog deliberately keeps
# unknown prices absent; config overrides remain authoritative.
def _price_from_catalog(raw: Mapping[str, float | None]) -> Price:
    input_price = raw.get("input")
    output_price = raw.get("output")
    cache_read = raw.get("cache_read")
    cache_write = raw.get("cache_write")
    if input_price is None or output_price is None:
        raise ValueError("catalog prices require input and output values")
    return Price(
        input=float(input_price),
        output=float(output_price),
        cache_read=float(cache_read) if cache_read is not None else None,
        cache_write=float(cache_write) if cache_write is not None else None,
    )


BUILTIN_PRICES: dict[str, Price] = {
    model: _price_from_catalog(raw)
    for model, raw in MODEL_CATALOG_DATA.prices.items()
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
