"""Credit rate cards: frozen versions selected by the time the usage happened.

A price change is a new ``RateCard`` entry; existing entries are never edited.
``rates_for(occurred_at)`` returns the latest card effective at or before the
event's own occurrence time, so a late-arriving event is priced by the card that
was in force when it happened. This covers time-versioning only — per-org
grandfathering needs DB rows and is deferred.

Rates are credits per 1M tokens (1 credit = $0.01), anchored at 1.5x the
per-token price HumR would pay on OpenRouter for the equivalent model. They are
product prices, not costs: the eventual Codex to per-token provider switch is an
internal swap, not a customer-visible repricing. The four token buckets are
disjoint and priced separately — a blended per-token rate misprices cache-heavy
agent workloads.

Keys are curated model-family prefixes rather than exact ids. The broker reports
whatever id the Codex backend chose, and observed traffic already carries dated
snapshots (``gpt-5.4-mini-2026-03-17``) that rotate without notice, so exact
matching would stall rating on every rotation. Matching is longest-prefix-wins
at a ``-`` boundary; a genuinely new family (``gpt-6``, and equally a next
version like ``gpt-5.55``) matches nothing and refuses. Suffixes are not assumed
cosmetic: the ``gpt-5.6`` celestial variants (``-sol``, ``-terra``, ``-luna``)
are distinct price tiers spanning 25x, so each is its own family and there is
deliberately no bare ``gpt-5.6`` entry — an unseen variant (``gpt-5.6-nova``)
must refuse, not price at another tier's rate. One guard on top: an id carrying
a tier token its matched prefix lacks is refused rather than priced, because a
hypothetical ``gpt-5.5-mini`` silently priced at ``gpt-5.5`` rates is the
dangerous mismatch. Prefix matching is a Codex-era bridge; after the provider
switch HumR chooses the ids it calls.
"""

import datetime
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

# Rates are quoted per this many tokens.
TOKENS_PER_RATE_UNIT = Decimal(1_000_000)

# Tokens that name a cheaper or dearer tier of the same family. An id carrying
# one its matched prefix does not is a different product at a different price.
TIER_TOKENS = frozenset({"mini", "nano", "pro"})


@dataclass(frozen=True)
class LlmRates:
    """Credits per 1M tokens for one model family's four disjoint token buckets."""

    input: Decimal
    cache_read: Decimal
    output: Decimal
    cache_write: Decimal


@dataclass(frozen=True)
class RateCard:
    """One frozen set of prices and the instant it takes effect."""

    version: str
    effective_from: datetime.datetime
    llm: dict[str, LlmRates]


@dataclass(frozen=True)
class LlmRateLookup:
    """Outcome of matching one observed model id against a card's families.

    ``rates`` is None whenever the id must not be priced, and ``refusal_reason``
    then carries the log-ready explanation. A refusal is never a charge of zero:
    the event stays unrated and is retried until a card covers it.
    """

    family: str | None
    rates: LlmRates | None
    refusal_reason: str | None


def _llm_rates(input_: str, cache_read: str, output: str, cache_write: str) -> LlmRates:
    """Build LlmRates from string literals, never binary floats."""
    return LlmRates(
        input=Decimal(input_),
        cache_read=Decimal(cache_read),
        output=Decimal(output),
        cache_write=Decimal(cache_write),
    )


# v1's provider prices were verified live 2026-07-30; its
# effective_from is deliberately earlier than any usage the rating job can see,
# because with no card before it an uncovered event could only go unpriced
# forever. The date matters only relative to the cards that follow.
#
# Reasoning tokens fold into output (matching OpenAI billing) and are not priced
# separately. Cache writes are 0: OpenAI does not charge them, and the broker
# records the provider's field as reported, with no established relationship to
# input_tokens, so rating must not invent one.
RATE_CARDS: tuple[RateCard, ...] = (
    RateCard(
        version="v1",
        effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        llm={
            "gpt-5.5": _llm_rates(input_="750", cache_read="75", output="4500", cache_write="0"),
            "gpt-5.6-sol": _llm_rates(input_="750", cache_read="75", output="4500", cache_write="0"),
            "gpt-5.6-terra": _llm_rates(input_="300", cache_read="30", output="1800", cache_write="0"),
            "gpt-5.6-luna": _llm_rates(input_="30", cache_read="3", output="180", cache_write="0"),
            "gpt-5.4-mini": _llm_rates(input_="110", cache_read="11", output="675", cache_write="0"),
        },
    ),
)


def rates_for(occurred_at: datetime.datetime) -> RateCard | None:
    """The card in force when the usage happened, or None if no card covers that instant."""
    eligible = [card for card in RATE_CARDS if card.effective_from <= occurred_at]
    if not eligible:
        return None
    return max(eligible, key=lambda card: card.effective_from)


def look_up_llm_rates(card: RateCard, model_id: str) -> LlmRateLookup:
    """Match an observed model id to its family's rates, or refuse it with a reason."""
    family = _longest_family_prefix(families=card.llm.keys(), model_id=model_id)
    if family is None:
        return LlmRateLookup(family=None, rates=None, refusal_reason="matches no model family on the rate card")

    stray_tiers = _unmatched_tier_tokens(family=family, model_id=model_id)
    if stray_tiers:
        reason = f"carries tier token(s) {sorted(stray_tiers)} that family {family!r} does not"
        return LlmRateLookup(family=family, rates=None, refusal_reason=reason)

    return LlmRateLookup(family=family, rates=card.llm[family], refusal_reason=None)


def _longest_family_prefix(families: Iterable[str], model_id: str) -> str | None:
    """The longest family the id extends at a '-' boundary (or equals exactly)."""
    matches = [family for family in families if model_id == family or model_id.startswith(f"{family}-")]
    if not matches:
        return None
    return max(matches, key=len)


def _unmatched_tier_tokens(family: str, model_id: str) -> set[str]:
    """Tier tokens the observed id carries that its matched family does not."""
    return (set(model_id.split("-")) & TIER_TOKENS) - set(family.split("-"))
