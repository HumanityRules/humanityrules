"""The entitlement snapshot each agent's integrations broker enforces against.

One small fact set — what is left, what a period grants, when it renews, which
plan, and whether spending must stop — computed from the balance the rating job
maintains and the organization's effective plan. The broker caches it and
refreshes event-driven: every usage-event post returns a fresh copy, and the
runtime GET serves idle organizations whose cache went stale.

Exhaustion is a floor at −5% of the plan's grant, not zero. Delayed events and
in-flight turns make exact-zero enforcement dishonest without reservation
machinery, so a fixed negative allowance is both simpler and truthful. Renewal
writes the balance off and re-grants, so grace overspend is absorbed there and
the new period starts at the full grant.

``renewal_date`` is the Stripe mirror's period end while the subscription still
keeps the organization on Operator (``BillingSubscription.OPERATOR_STATUSES``).
A trial has no subscription and a canceled one no longer renews, so both read
as None.
"""

from dataclasses import dataclass
from decimal import Decimal

from humanityrules_app import models
from humanityrules_app.services.billing import plans

# How far past zero an organization may spend before the broker refuses model
# calls, as a fraction of its plan's grant.
EXHAUSTION_GRACE_FRACTION = Decimal("0.05")


@dataclass(frozen=True)
class EntitlementSnapshot:
    """What the broker needs to decide whether this organization may spend."""

    credits_remaining: int
    monthly_grant: int
    renewal_date: str | None
    plan: str
    exhausted: bool


def exhaustion_floor(monthly_grant: int) -> Decimal:
    """The balance at or below which spending stops: −5% of the plan's grant."""
    return -(Decimal(monthly_grant) * EXHAUSTION_GRACE_FRACTION)


def entitlement_snapshot(organization: models.Organization) -> EntitlementSnapshot:
    """The organization's current spending entitlement, as the broker enforces it."""
    plan = plans.effective_plan(organization=organization)
    balance = models.BillingBalance.objects.filter(organization=organization).first()
    credits_remaining = balance.credits if balance is not None else Decimal(0)
    subscription = models.BillingSubscription.objects.filter(
        organization=organization,
        status__in=models.BillingSubscription.OPERATOR_STATUSES,
        current_period_end__isnull=False,
    ).first()
    renewal_date = subscription.current_period_end.date().isoformat() if subscription is not None else None
    return EntitlementSnapshot(
        credits_remaining=int(credits_remaining),
        monthly_grant=plan.monthly_credit_grant,
        renewal_date=renewal_date,
        plan=organization.plan,
        exhausted=credits_remaining <= exhaustion_floor(monthly_grant=plan.monthly_credit_grant),
    )


def snapshot_payload(organization: models.Organization) -> dict:
    """The snapshot as the JSON body the broker reads."""
    snapshot = entitlement_snapshot(organization=organization)
    return {
        "credits_remaining": snapshot.credits_remaining,
        "monthly_grant": snapshot.monthly_grant,
        "renewal_date": snapshot.renewal_date,
        "plan": snapshot.plan,
        "exhausted": snapshot.exhausted,
    }
