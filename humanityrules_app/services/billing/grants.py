"""How HumR posts plan credits and expires the previous billing period.

HumR bills in credits (1 credit ≈ $0.01 of rated usage). The append-only
``BillingLedgerEntry`` is the money record; ``BillingBalance`` is its cached
sum and the row every writer locks so concurrent writers serialize for one
organization. A ledger entry is safe only when its writer holds that lock and
the entry carries a unique idempotency key.

The one-time trial allotment is a single positive grant. An Operator renewal
and an ex-customer's restarted trial are each an atomic pair under one balance
lock: write off the previous balance, then post the new grant, resetting the
balance to that grant. Writing off a positive balance expires unused
entitlement; writing off a negative balance forgives grace overspend bounded
by the enforcement floor.

Stripe delivers webhooks at least once, so the event that triggered either
reset can arrive again — possibly days later, after the customer has spent
from the new grant. Each reset therefore checks for its grant entry first and,
when it already exists, writes nothing at all: re-running just the write-off
would wipe those newly spent credits.

All grant paths read the organization's effective plan after overrides. Grant
metadata stamps the plan and config version that supplied the amount, leaving
old ledger rows explainable after the in-code registry changes.
"""

import datetime
import logging
from decimal import Decimal
from uuid import UUID

from django.db import transaction

from humanityrules_app import models
from humanityrules_app.services.billing import plans

logger = logging.getLogger(__name__)


def _write_locked_entry(
    balance: models.BillingBalance,
    entry_type: str,
    amount: Decimal,
    idempotency_key: str,
    description: str,
    metadata: dict,
) -> bool:
    """Post one grant or expiry using an already-locked balance; False means the key already exists."""
    if entry_type == models.BillingLedgerEntry.Type.GRANT and amount <= 0:
        raise ValueError(f"a grant must be positive, got {amount}")
    if entry_type == models.BillingLedgerEntry.Type.EXPIRY and amount == 0:
        raise ValueError(f"an expiry must be non-zero, got {amount}")
    if entry_type not in {models.BillingLedgerEntry.Type.GRANT, models.BillingLedgerEntry.Type.EXPIRY}:
        raise ValueError(f"grants.py posts only grant and expiry entries, got {entry_type!r}")

    entry_exists = models.BillingLedgerEntry.objects.filter(
        organization_id=balance.organization_id,
        idempotency_key=idempotency_key,
    ).exists()
    if entry_exists:
        logger.info(f"entry {idempotency_key} already posted for organization {balance.organization_id}; no-op")
        return False

    models.BillingLedgerEntry.objects.create(
        organization_id=balance.organization_id,
        type=entry_type,
        amount=amount,
        idempotency_key=idempotency_key,
        usage_event=None,
        description=description,
        metadata=metadata,
    )
    previous_credits = balance.credits
    balance.credits = previous_credits + amount
    balance.save(update_fields=["credits", "updated_at"])
    logger.info(
        f"entry {idempotency_key} posted {amount} credit(s) to organization {balance.organization_id}: "
        f"balance {previous_credits} -> {balance.credits}"
    )
    return True


def _write_grant(organization_id: UUID, credits: Decimal, idempotency_key: str, description: str, metadata: dict) -> bool:
    """Post a positive credit grant under the balance lock; False when the key already exists."""
    models.BillingBalance.objects.get_or_create(
        organization_id=organization_id,
        defaults={"credits": Decimal(0)},
    )
    with transaction.atomic():
        balance = (
            models.BillingBalance.objects
            .select_for_update()
            .filter(organization_id=organization_id)
            .get()
        )
        return _write_locked_entry(
            balance=balance,
            entry_type=models.BillingLedgerEntry.Type.GRANT,
            amount=credits,
            idempotency_key=idempotency_key,
            description=description,
            metadata=metadata,
        )


def grant_trial_credits(organization: models.Organization) -> bool:
    """Write an organization's one-time trial grant; False when it already has one.

    The amount comes from the organization's effective plan, so a comped org
    whose overrides raise the grant is backfilled with the raised number rather
    than the registry default.
    """
    plan = plans.effective_plan(organization=organization)
    return _write_grant(
        organization_id=organization.id,
        credits=Decimal(plan.monthly_credit_grant),
        idempotency_key=f"grant:trial:{organization.id}",
        description="Trial credits",
        metadata={"plan": organization.plan, "plan_version": plans.PLAN_VERSION},
    )


def restart_trial_credits(organization: models.Organization, stripe_event_id: str, stripe_subscription_id: str) -> bool:
    """Reset an ex-customer's balance to the effective Trial grant; False when that Stripe event already reset it.

    Idempotency is keyed on the triggering webhook event rather than on the
    subscription, because one subscription can drop out of Operator more than
    once: a failed payment can recover back to Operator before the
    subscription is later canceled outright, and each drop is its own flip
    that needs its own write-off-and-grant. Keying on the subscription would
    make the second flip's reset look like a replay of the first and skip it.
    """
    grant_key = f"grant:trial-reset:{stripe_event_id}"
    expiry_key = f"expiry:trial-reset:{stripe_event_id}"
    plan = plans.effective_plan(organization=organization)
    grant_amount = Decimal(plan.monthly_credit_grant)

    if grant_amount <= 0:
        raise ValueError(f"a restarted Trial grant must be positive, got {grant_amount}")

    models.BillingBalance.objects.get_or_create(
        organization=organization,
        defaults={"credits": Decimal(0)},
    )
    with transaction.atomic():
        balance = (
            models.BillingBalance.objects
            .select_for_update()
            .filter(organization=organization)
            .get()
        )
        grant_exists = models.BillingLedgerEntry.objects.filter(
            organization=organization,
            idempotency_key=grant_key,
        ).exists()
        if grant_exists:
            logger.info(f"Trial restart {grant_key} already posted for organization {organization.id}; no-op")
            return False

        if balance.credits != 0:
            _write_locked_entry(
                balance=balance,
                entry_type=models.BillingLedgerEntry.Type.EXPIRY,
                amount=-balance.credits,
                idempotency_key=expiry_key,
                description="Subscription-end balance written off for Trial restart",
                metadata={"subscription_id": stripe_subscription_id},
            )

        return _write_locked_entry(
            balance=balance,
            entry_type=models.BillingLedgerEntry.Type.GRANT,
            amount=grant_amount,
            idempotency_key=grant_key,
            description="Trial credits after subscription ended",
            metadata={"plan": organization.plan, "plan_version": plans.PLAN_VERSION},
        )


def grant_operator_period_credits(organization: models.Organization, stripe_subscription_id: str, period_start: datetime.date) -> bool:
    """Reset the balance to one Operator period's grant; False when that period already posted."""
    period = period_start.isoformat()
    grant_key = f"grant:{stripe_subscription_id}:{period}"
    expiry_key = f"expiry:{stripe_subscription_id}:{period}"
    plan = plans.effective_plan(organization=organization)
    grant_amount = Decimal(plan.monthly_credit_grant)

    if grant_amount <= 0:
        raise ValueError(f"an Operator period grant must be positive, got {grant_amount}")

    models.BillingBalance.objects.get_or_create(
        organization=organization,
        defaults={"credits": Decimal(0)},
    )
    with transaction.atomic():
        balance = (
            models.BillingBalance.objects
            .select_for_update()
            .filter(organization=organization)
            .get()
        )
        grant_exists = models.BillingLedgerEntry.objects.filter(
            organization=organization,
            idempotency_key=grant_key,
        ).exists()
        if grant_exists:
            logger.info(f"Operator period {grant_key} already posted for organization {organization.id}; no-op")
            return False

        if balance.credits != 0:
            _write_locked_entry(
                balance=balance,
                entry_type=models.BillingLedgerEntry.Type.EXPIRY,
                amount=-balance.credits,
                idempotency_key=expiry_key,
                description="Previous period balance written off at renewal",
                metadata={"subscription_id": stripe_subscription_id, "period_start": period},
            )

        return _write_locked_entry(
            balance=balance,
            entry_type=models.BillingLedgerEntry.Type.GRANT,
            amount=grant_amount,
            idempotency_key=grant_key,
            description="Operator monthly credits",
            metadata={"plan": organization.plan, "plan_version": plans.PLAN_VERSION},
        )
