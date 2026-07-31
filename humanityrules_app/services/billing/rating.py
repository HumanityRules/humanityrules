"""Turn unrated usage events into ledger charges — the only code that converts usage to credits.

One global periodic tick in the CP job worker. Each pass picks up events left
unrated inside the last 30 days and, per organization in its own transaction,
locks the balance row, folds those events into that organization's per-app day
charge entries, and stamps ``rated_at``. The horizon bounds the scan against
permanently unrateable rows with no quarantine flag; events unpriced for longer
never charge, because undercharging is the accepted failure mode.

A refusal is never a charge of zero. An event whose source or model family no
card covers stays unrated and is retried every pass, so adding the family to the
rate card prices it retroactively at its own ``occurred_at`` card.

Charges land in one entry per organization, app and *rating* date — the posting
date, taken once inside the transaction, never the event's ``occurred_at``. The
job can therefore only ever write to today's entry, which makes past days
immutable with no close signal or grace window. A late event is still priced by
the card at its ``occurred_at``; it just posts to a later day, with the true
usage time in the event row.

Credits are integers at rest but events rate fractionally, so each day entry
keeps the exact Decimal sum of everything folded into it in its metadata and
re-derives ``amount = round(exact_sum)`` on every upsert, moving the balance by
the delta of the rounded amounts. Rounding per event is forbidden: it biases
systematically, since a day of 0.4-credit events would charge nothing.
Re-deriving keeps every amount within half a credit of truth with no drift and
holds ``balance == SUM(entries)`` exactly.

Enforcement reads the balance this job maintains, so the cadence is a product
constraint, and the verbose logging below is a design requirement rather than
noise: the shadow period is validated from these lines.
"""

import datetime
import decimal
import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.services.billing import rates

logger = logging.getLogger(__name__)

# How far back a pass looks for unrated events.
RATING_HORIZON = datetime.timedelta(days=30)

# Events folded per organization per pass. A capped pass leaves the remainder
# for the next tick a minute later instead of holding one enormous transaction,
# and its balance lock, open.
MAX_EVENTS_PER_ORGANIZATION_PASS = 2000

_SOURCE_LLM = "llm"

# The priceable llm token buckets, each against its own rate. reasoning_tokens
# is absent on purpose: it is already inside output_tokens.
_LLM_PRICED_BUCKETS = ("input_tokens", "cache_read_tokens", "output_tokens", "cache_write_tokens")


@dataclass(frozen=True)
class RatedEvent:
    """One event priced against one card: the credits it consumes and the audit facts behind them."""

    credits: Decimal  # Positive magnitude; the ledger entry carries the sign.
    rate_card_version: str
    family: str
    priced_tokens: dict[str, int]


def day_charge_idempotency_key(organization_id: UUID, app_id: UUID, posting_date: datetime.date) -> str:
    """The org/app/day charge entry's key — also the handle rating reopens today's entry by."""
    return f"charge:{organization_id}:{app_id}:{posting_date.isoformat()}"


def rate_event(event: models.BillingUsageEvent) -> RatedEvent | None:
    """Price one usage event, or refuse it (logging why) so it stays unrated and is retried."""
    card = rates.rates_for(occurred_at=event.occurred_at)
    if card is None:
        logger.error(
            f"rating refused event {event.idempotency_key} org={event.organization_id}: "
            f"no rate card in effect at {event.occurred_at.isoformat()}"
        )
        return None

    if event.source != _SOURCE_LLM:
        logger.error(
            f"rating refused event {event.idempotency_key} org={event.organization_id}: "
            f"source {event.source!r} subkey {event.subkey!r} has no rate card entry"
        )
        return None

    lookup = rates.look_up_llm_rates(card=card, model_id=event.subkey)
    if lookup.rates is None:
        logger.error(
            f"rating refused event {event.idempotency_key} org={event.organization_id}: "
            f"subkey {event.subkey!r} {lookup.refusal_reason} (card {card.version})"
        )
        return None

    priced_tokens = {name: _token_count(event=event, name=name) for name in _LLM_PRICED_BUCKETS}
    credits = (
        priced_tokens["input_tokens"] * lookup.rates.input
        + priced_tokens["cache_read_tokens"] * lookup.rates.cache_read
        + priced_tokens["output_tokens"] * lookup.rates.output
        + priced_tokens["cache_write_tokens"] * lookup.rates.cache_write
    ) / rates.TOKENS_PER_RATE_UNIT
    return RatedEvent(
        credits=credits,
        rate_card_version=card.version,
        family=lookup.family,
        priced_tokens=priced_tokens,
    )


def rate_pending_events(now: datetime.datetime) -> None:
    """One rating pass: fold every organization's unrated recent events into ledger charges."""
    horizon_start = now - RATING_HORIZON
    organization_ids = list(
        models.BillingUsageEvent.objects
        .filter(rated_at__isnull=True, occurred_at__gte=horizon_start)
        .values_list("organization_id", flat=True)
        .distinct()
    )
    if not organization_ids:
        logger.info(f"rating pass found no unrated events occurring since {horizon_start.isoformat()}")
        return

    logger.info(
        f"rating pass picked up {len(organization_ids)} organization(s) with unrated events "
        f"occurring since {horizon_start.isoformat()}"
    )
    for organization_id in organization_ids:
        try:
            _rate_organization(organization_id=organization_id, horizon_start=horizon_start)
        except Exception:
            # One organization's failure rolls back only its own transaction;
            # its events stay unrated for the next pass and the rest still rate.
            logger.exception(f"rating pass failed for organization {organization_id}")


def _rate_organization(organization_id: UUID, horizon_start: datetime.datetime) -> None:
    """Rate one organization's pending events under its balance lock, in one transaction."""
    models.BillingBalance.objects.get_or_create(organization_id=organization_id, defaults={"credits": Decimal(0)})

    with transaction.atomic():
        balance = (
            models.BillingBalance.objects
            .select_for_update(skip_locked=True)
            .filter(organization_id=organization_id)
            .first()
        )
        if balance is None:
            logger.info(f"rating skipped organization {organization_id}: another pass holds its balance row")
            return

        # The posting date is derived once here, so every entry this transaction
        # touches belongs to the same day and past days stay immutable.
        posted_at = timezone.now()
        posting_date = posted_at.astimezone(datetime.UTC).date()

        events = list(
            models.BillingUsageEvent.objects
            .select_for_update(skip_locked=True)
            .filter(organization_id=organization_id, rated_at__isnull=True, occurred_at__gte=horizon_start)
            .order_by("occurred_at", "id")[:MAX_EVENTS_PER_ORGANIZATION_PASS]
        )
        if not events:
            logger.info(f"rating found nothing left to rate for organization {organization_id}")
            return

        logger.info(
            f"rating organization {organization_id}: picked up {len(events)} unrated event(s), "
            f"posting to {posting_date.isoformat()}"
        )
        if len(events) == MAX_EVENTS_PER_ORGANIZATION_PASS:
            logger.info(
                f"rating organization {organization_id} hit the {MAX_EVENTS_PER_ORGANIZATION_PASS}-event pass cap; "
                f"the remainder rates on the next tick"
            )

        rated_by_app: dict[UUID, list[RatedEvent]] = {}
        app_slugs: dict[UUID, str] = {}
        rated_event_ids: list[UUID] = []
        refused_count = 0
        for event in events:
            rated = rate_event(event=event)
            if rated is None:
                refused_count += 1
                continue
            logger.info(
                f"rating priced event {event.idempotency_key} org={organization_id} app={event.app_id} "
                f"family={rated.family} card={rated.rate_card_version} tokens={rated.priced_tokens} "
                f"credits={rated.credits}"
            )
            rated_by_app.setdefault(event.app_id, []).append(rated)
            app_slugs[event.app_id] = event.app_slug
            rated_event_ids.append(event.id)

        if refused_count:
            logger.error(
                f"rating organization {organization_id}: {refused_count} of {len(events)} event(s) refused and "
                f"left unrated; they retry every pass until the horizon"
            )
        if not rated_event_ids:
            return

        balance_delta = Decimal(0)
        for app_id, rated_events in rated_by_app.items():
            balance_delta += _upsert_day_charge(
                organization_id=organization_id,
                app_id=app_id,
                app_slug=app_slugs[app_id],
                posting_date=posting_date,
                rated_events=rated_events,
            )

        # rated_at is the posting instant, so an event's UTC rated_at date names
        # the day charge entry it landed in.
        marked = models.BillingUsageEvent.objects.filter(
            organization_id=organization_id, id__in=rated_event_ids,
        ).update(rated_at=posted_at)
        logger.info(f"rating organization {organization_id}: marked {marked} event(s) rated at {posted_at.isoformat()}")

        previous_credits = balance.credits
        balance.credits = previous_credits + balance_delta
        balance.save(update_fields=["credits", "updated_at"])
        logger.info(
            f"rating organization {organization_id}: balance {previous_credits} -> {balance.credits} "
            f"(delta {balance_delta})"
        )


def _upsert_day_charge(
    organization_id: UUID,
    app_id: UUID,
    app_slug: str,
    posting_date: datetime.date,
    rated_events: list[RatedEvent],
) -> Decimal:
    """Fold one app's freshly priced events into its day charge entry; return the balance delta."""
    idempotency_key = day_charge_idempotency_key(
        organization_id=organization_id, app_id=app_id, posting_date=posting_date,
    )
    entry = (
        models.BillingLedgerEntry.objects
        .filter(organization_id=organization_id, idempotency_key=idempotency_key)
        .first()
    )
    previous_metadata = entry.metadata if entry is not None else {}
    previous_amount = entry.amount if entry is not None else Decimal(0)
    metadata = _accumulated_metadata(previous_metadata=previous_metadata, rated_events=rated_events)
    exact_credits = Decimal(metadata["exact_credits"])
    amount = exact_credits.quantize(Decimal(1), rounding=decimal.ROUND_HALF_EVEN)
    description = f"Agent usage on {posting_date.isoformat()} ({app_slug})"

    if entry is None:
        models.BillingLedgerEntry.objects.create(
            organization_id=organization_id,
            type=models.BillingLedgerEntry.Type.CHARGE,
            amount=amount,
            idempotency_key=idempotency_key,
            usage_event=None,
            description=description,
            metadata=metadata,
        )
        logger.info(
            f"rating opened day charge {idempotency_key}: amount {amount}, exact {exact_credits}, "
            f"{metadata['event_count']} event(s)"
        )
    else:
        entry.amount = amount
        entry.description = description
        entry.metadata = metadata
        entry.save(update_fields=["amount", "description", "metadata", "updated_at"])
        logger.info(
            f"rating accumulated day charge {idempotency_key}: amount {previous_amount} -> {amount}, "
            f"exact {exact_credits}, {metadata['event_count']} event(s)"
        )

    return amount - previous_amount


def _accumulated_metadata(previous_metadata: dict, rated_events: list[RatedEvent]) -> dict:
    """Fold newly priced events into the day entry's exact sum, version stamps and usage breakdown.

    Amounts are stored signed and negative, so the breakdown's per-family credits
    sum to ``exact_credits``, which in turn is what ``amount`` rounds. Rate card
    versions are a list because a day entry can hold both fresh events and late
    ones priced by an older card.
    """
    exact_credits = Decimal(previous_metadata.get("exact_credits", "0"))
    event_count = previous_metadata.get("event_count", 0)
    card_versions = set(previous_metadata.get("rate_card_versions", []))
    usage = {family: dict(line) for family, line in previous_metadata.get("usage", {}).items()}

    for rated in rated_events:
        exact_credits -= rated.credits
        event_count += 1
        card_versions.add(rated.rate_card_version)
        line = usage.setdefault(rated.family, _blank_usage_line())
        for bucket, count in rated.priced_tokens.items():
            line[bucket] = line.get(bucket, 0) + count
        line["event_count"] = line.get("event_count", 0) + 1
        line["credits"] = str(Decimal(line.get("credits", "0")) - rated.credits)

    return {
        "exact_credits": str(exact_credits),
        "event_count": event_count,
        "rate_card_versions": sorted(card_versions),
        "usage": usage,
    }


def _blank_usage_line() -> dict:
    """An empty per-family breakdown line."""
    line: dict = {bucket: 0 for bucket in _LLM_PRICED_BUCKETS}
    line["event_count"] = 0
    line["credits"] = "0"
    return line


def _token_count(event: models.BillingUsageEvent, name: str) -> int:
    """One token bucket; an absent key reads as 0, as an older broker had nothing to say about it."""
    count = event.quantities.get(name, 0)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        # Ingest validates quantities, so a malformed value here is a writer bug
        # that would otherwise underprice silently.
        logger.error(
            f"rating read {name}={count!r} as 0 on event {event.idempotency_key} "
            f"org={event.organization_id}: malformed quantity that ingest validation should have rejected"
        )
        return 0
    return count
