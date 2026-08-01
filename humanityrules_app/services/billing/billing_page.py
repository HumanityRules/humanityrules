"""Assemble the plain, customer-facing values rendered on the billing settings page.

The control-plane view passes this frozen snapshot directly to the template.
Keeping balance, ledger-period, plan, and subscription interpretation here means
the template only presents values and the view never needs Stripe's SDK.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum

from humanityrules_app import models
from humanityrules_app.services.billing import plans


@dataclass(frozen=True)
class BillingPageContext:
    """Every value the organization billing page is allowed to render."""

    plan_name: str
    credits_remaining: int
    monthly_grant: int
    current_period_burn: int
    renewal_date: datetime.date | None
    show_upgrade: bool
    show_manage_billing: bool
    stripe_configured: bool
    operator_price_usd: str


def billing_page_context(organization: models.Organization) -> BillingPageContext:
    """Build the billing page snapshot for one organization.

    Usage starts at an Operator subscription's current-period start when its
    status still grants Operator access. Without that anchor, it starts at the
    organization's latest grant ledger entry, or at organization creation when
    no grant exists. This makes subscription periods authoritative while still
    giving one-time trials and hand-managed plans a meaningful usage period.
    """
    effective_plan = plans.effective_plan(organization=organization)
    balance = models.BillingBalance.objects.filter(organization=organization).first()
    subscription = models.BillingSubscription.objects.filter(organization=organization).first()
    operator_subscription = (
        subscription is not None
        and subscription.status in models.BillingSubscription.OPERATOR_STATUSES
    )

    if operator_subscription and subscription.current_period_start is not None:
        period_anchor = subscription.current_period_start
    else:
        latest_grant_at = (
            models.BillingLedgerEntry.objects
            .filter(organization=organization, type=models.BillingLedgerEntry.Type.GRANT)
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        period_anchor = latest_grant_at if latest_grant_at is not None else organization.created_at

    charge_total = (
        models.BillingLedgerEntry.objects
        .filter(
            organization=organization,
            type=models.BillingLedgerEntry.Type.CHARGE,
            created_at__gte=period_anchor,
        )
        .aggregate(total=Sum("amount"))["total"]
        or Decimal(0)
    )
    credits_remaining = int(balance.credits) if balance is not None else 0
    renewal_date = (
        subscription.current_period_end.date()
        if operator_subscription and subscription.current_period_end is not None
        else None
    )
    operator_price = plans.PLANS[plans.OPERATOR].price_usd_month
    if operator_price is None:
        raise RuntimeError("the Operator plan must have a monthly USD price")

    return BillingPageContext(
        plan_name=organization.plan.title(),
        credits_remaining=max(credits_remaining, 0),
        monthly_grant=effective_plan.monthly_credit_grant,
        current_period_burn=int(abs(charge_total)),
        renewal_date=renewal_date,
        show_upgrade=organization.plan == plans.TRIAL,
        show_manage_billing=subscription is not None and bool(subscription.stripe_customer_id),
        stripe_configured=bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_OPERATOR_PRICE_ID),
        operator_price_usd=str(operator_price),
    )
