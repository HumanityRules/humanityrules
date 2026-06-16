"""Bedrock invocation pricing: ARN normalization, the per-model rate table, and ``cost_for``.

This module is the single source of truth for turning a Bedrock ``modelId`` plus token counts into a
USD cost. The four token buckets in the invocation log are disjoint and each priced separately —
``inputTokenCount``, ``outputTokenCount``, ``cacheWriteInputTokenCount``, ``cacheReadInputTokenCount``
— they are NOT multiples of the input price.

RATE PROVENANCE — the rates below are absolute $/1M-token amounts copied from the AWS Bedrock pricing
page (https://aws.amazon.com/bedrock/pricing/), region **US East (Ohio)**, captured **2026-06-15**.
They are not derived from any ratio. Two tiers are stored as independently-listed absolute rates:
``global`` (``global.`` inference profiles) and ``geo`` (``us.``/``eu.``/``au.``/``jp.`` geographic and
in-region profiles); the page lists ``geo`` ≈ ``global`` × 1.1 but we store both verbatim rather than
multiply. **To refresh:** re-copy the two pricing-page tables for these models and update ``_RATES``.

Modeling choices (see ``docs/app_cost_tracking_design.md``):
- Cache-write uses the **5-minute-TTL** column. The invocation log exposes a single
  ``cacheWriteInputTokenCount`` with no TTL, and we deliberately do not model first-party cache-TTL
  tiers, so the default (5 min) rate is applied.
- Rates are US pricing. Cost for a non-US **source** region (billing is by source region, where logs
  land — not ``inferenceRegion``) is returned with ``estimate=True``.
- An unknown model or tier yields ``priced=False`` (cost 0) — never a wrong price.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ModelRates:
    """Per-1M-token USD rates for one model+tier. ``None`` = category not priced for this model."""

    input: Decimal | None
    output: Decimal | None
    cache_write: Decimal | None
    cache_read: Decimal | None


def _rates(input_: str, output: str, cache_write: str | None, cache_read: str | None) -> ModelRates:
    """Build a ModelRates from string literals (column order: input, output, cache-write-5m, cache-read)."""
    return ModelRates(
        input=Decimal(input_),
        output=Decimal(output),
        cache_write=Decimal(cache_write) if cache_write is not None else None,
        cache_read=Decimal(cache_read) if cache_read is not None else None,
    )


# tier -> normalized model key (foundation-model id) -> ModelRates ($/1M tokens). See RATE PROVENANCE.
_RATES: dict[str, dict[str, ModelRates]] = {
    "global": {
        "anthropic.claude-fable-5": _rates("10.00", "50.00", "12.50", "1.00"),
        "anthropic.claude-opus-4-8": _rates("5.00", "25.00", "6.25", "0.50"),
        "anthropic.claude-opus-4-7": _rates("5.00", "25.00", "6.25", "0.50"),
        "anthropic.claude-sonnet-4-6": _rates("3.00", "15.00", "3.75", "0.30"),
        "anthropic.claude-opus-4-6": _rates("5.00", "25.00", "6.25", "0.50"),
        "anthropic.claude-opus-4-5": _rates("5.00", "25.00", "6.25", "0.50"),
        "anthropic.claude-haiku-4-5": _rates("1.00", "5.00", "1.25", "0.10"),
        "anthropic.claude-sonnet-4-5": _rates("3.00", "15.00", "3.75", "0.30"),
        "anthropic.claude-sonnet-4": _rates("3.00", "15.00", "3.75", "0.30"),
    },
    "geo": {
        "anthropic.claude-fable-5": _rates("11.00", "55.00", "13.75", "1.10"),
        "anthropic.claude-opus-4-8": _rates("5.50", "27.50", "6.875", "0.55"),
        "anthropic.claude-opus-4-7": _rates("5.50", "27.50", "6.875", "0.55"),
        "anthropic.claude-sonnet-4-6": _rates("3.30", "16.50", "4.125", "0.33"),
        "anthropic.claude-opus-4-6": _rates("5.50", "27.50", "6.875", "0.55"),
        "anthropic.claude-opus-4-5": _rates("5.50", "27.50", "6.875", "0.55"),
        "anthropic.claude-haiku-4-5": _rates("1.10", "5.50", "1.375", "0.11"),
        "anthropic.claude-sonnet-4-5": _rates("3.30", "16.50", "4.125", "0.33"),
        "anthropic.claude-opus-4-1": _rates("15.00", "75.00", "18.75", "1.50"),
        "anthropic.claude-opus-4": _rates("15.00", "75.00", "18.75", "1.50"),
        "anthropic.claude-sonnet-4": _rates("3.00", "15.00", "3.75", "0.30"),
        "anthropic.claude-3-7-sonnet": _rates("3.00", "15.00", "3.75", "0.30"),
        "anthropic.claude-3-5-sonnet-v2": _rates("3.00", "15.00", "3.75", "0.30"),
        "anthropic.claude-3-5-sonnet": _rates("3.00", "15.00", None, None),
        "anthropic.claude-3-5-haiku": _rates("0.80", "4.00", "1.00", "0.08"),
        "anthropic.claude-3-haiku": _rates("0.25", "1.25", None, None),
    },
}

_PROVIDER_MARKER = "anthropic."
_TOKENS_PER_UNIT = Decimal(1_000_000)


@dataclass(frozen=True)
class CostBreakdown:
    """Result of pricing one (model, tier, region, token-bucket) aggregate."""

    cost_usd: Decimal
    priced: bool  # True if a rate table existed for this model (in the requested or fallback tier)
    estimate: bool  # priced but with a caveat (cross-tier fallback, non-US region, or missing cache rate)


def normalize_model_id(model_id: str) -> tuple[str, str]:
    """Split a Bedrock ``modelId``/ARN into ``(model_key, tier)``.

    ``model_key`` is the foundation-model id starting at ``anthropic.`` (the geo/global prefix stripped);
    ``tier`` is ``"global"`` or ``"geo"``. Logs carry the inference-profile ARN, e.g.
    ``arn:aws:bedrock:us-east-1:ACCT:inference-profile/us.anthropic.claude-sonnet-4-6`` → the last path
    segment is ``us.anthropic.claude-sonnet-4-6``, and the prefix (``us``/``global``/…) is the geo signal.
    A non-Anthropic id returns the bare segment as the key (it will simply be unpriced).
    """
    segment = model_id.rsplit("/", 1)[-1]
    marker_index = segment.find(_PROVIDER_MARKER)
    if marker_index == -1:
        return segment, "geo"
    model_key = segment[marker_index:]
    prefix = segment[:marker_index].rstrip(".")
    tier = "global" if prefix == "global" else "geo"
    return model_key, tier


def _is_us_region(region: str) -> bool:
    """True for US commercial regions (which share the captured rate table); GovCloud excluded."""
    return region.startswith("us-") and not region.startswith("us-gov-")


def _line_cost(tokens: int, rate_per_unit: Decimal | None) -> Decimal:
    """Cost for one token bucket; 0 when there are no tokens or no rate for the category."""
    if not tokens or rate_per_unit is None:
        return Decimal(0)
    return (Decimal(tokens) / _TOKENS_PER_UNIT) * rate_per_unit


def cost_for(
    model_id: str,
    source_region: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int,
    cache_read_tokens: int,
) -> CostBreakdown:
    """Price one aggregate of token buckets for a model in a source region. Sums all four buckets."""
    model_key, tier = normalize_model_id(model_id=model_id)
    estimate = False

    rates = _RATES.get(tier, {}).get(model_key)
    if rates is None:
        fallback_tier = "geo" if tier == "global" else "global"
        rates = _RATES.get(fallback_tier, {}).get(model_key)
        if rates is not None:
            estimate = True  # priced from the other tier; ~10% off
    if rates is None:
        return CostBreakdown(cost_usd=Decimal(0), priced=False, estimate=False)

    if not _is_us_region(region=source_region):
        estimate = True  # rate table is US pricing

    cost = (
        _line_cost(tokens=input_tokens, rate_per_unit=rates.input)
        + _line_cost(tokens=output_tokens, rate_per_unit=rates.output)
        + _line_cost(tokens=cache_write_tokens, rate_per_unit=rates.cache_write)
        + _line_cost(tokens=cache_read_tokens, rate_per_unit=rates.cache_read)
    )

    missing_cache_rate = (cache_write_tokens and rates.cache_write is None) or (
        cache_read_tokens and rates.cache_read is None
    )
    if missing_cache_rate:
        estimate = True  # undercounts: cache tokens present but no cache rate for this model

    return CostBreakdown(cost_usd=cost, priced=True, estimate=estimate)
