"""How HumR puts credits *into* an organization's account.

HumR bills in credits (1 credit ≈ $0.01 of rated usage). The source of truth
for how many credits an organization has is an append-only ledger
(``BillingLedgerEntry``): every addition or subtraction is a new row. The
``BillingBalance`` row is only a cached sum of that ledger — kept so we can
read the current total quickly and lock one row when writing. Charges
subtract; this module is the other direction — entries of type ``grant``,
which add credits.

Why that matters for this file: every credit movement, grant or charge, must
(1) take a ``SELECT FOR UPDATE`` on that org's balance row so concurrent writers
serialize, and (2) carry a unique idempotency key so retries, races, and
re-runs cannot double-apply. Skip either rule and the balance can diverge from
``SUM(ledger)``, which we only repair by hand. This module is the grant side of
that contract.

Callers of this write path include the one-time trial allotment at signup
(and the management command that backfills orgs that never got one), and
monthly Operator renewals from Stripe's invoice-paid webhook. The trial
amount comes from the org's effective plan (including any admin
``plan_overrides``), so an org whose trial grant was raised by an override
receives that raised number, not the registry default. Signup and backfill
share the key ``grant:trial:{org_id}``, so whichever runs first (or twice)
wins once — the second call is a no-op. Renewals use the same locked write
with a different, period-scoped key shape — not a separate "just bump the
balance" shortcut.
"""

import logging
from decimal import Decimal
from uuid import UUID

from django.db import transaction

from humanityrules_app import models
from humanityrules_app.services.billing import plans

logger = logging.getLogger(__name__)


def _write_grant(
    organization_id: UUID,
    credits: Decimal,
    idempotency_key: str,
    description: str,
    metadata: dict,
) -> bool:
    """Post a positive credit grant under the balance lock; False when the key already exists."""
    if credits <= 0:
        raise ValueError(f"a grant must be positive, got {credits}")

    models.BillingBalance.objects.get_or_create(
        organization_id=organization_id, defaults={"credits": Decimal(0)},
    )
    with transaction.atomic():
        balance = (
            models.BillingBalance.objects
            .select_for_update()
            .filter(organization_id=organization_id)
            .get()
        )
        already_granted = models.BillingLedgerEntry.objects.filter(
            organization_id=organization_id, idempotency_key=idempotency_key,
        ).exists()

        if already_granted:
            logger.info(f"grant {idempotency_key} already posted for organization {organization_id}; no-op")
            return False

        models.BillingLedgerEntry.objects.create(
            organization_id=organization_id,
            type=models.BillingLedgerEntry.Type.GRANT,
            amount=credits,
            idempotency_key=idempotency_key,
            usage_event=None,
            description=description,
            metadata=metadata,
        )
        previous_credits = balance.credits
        balance.credits = previous_credits + credits
        balance.save(update_fields=["credits", "updated_at"])
        logger.info(
            f"grant {idempotency_key} posted {credits} credit(s) to organization {organization_id}: "
            f"balance {previous_credits} -> {balance.credits}"
        )
        return True


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
