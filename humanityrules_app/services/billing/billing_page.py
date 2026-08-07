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

    plan_key: str
    plan_name: str
    show_credits: bool
    credits_remaining: int
    monthly_grant: int
    trial_runtime_days: int | None
    max_agents: int | None
    current_period_burn: int
    renewal_date: datetime.date | None
    plan_end_date: datetime.date | None
    show_upgrade: bool
    show_manage_billing: bool
    stripe_configured: bool
    operator_price_usd: str
    team_agent_price_usd: str


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
    subscription_keeps_operator = (
        subscription is not None
        and subscription.status in models.BillingSubscription.OPERATOR_STATUSES
    )

    if subscription_keeps_operator and subscription.current_period_start is not None:
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
    plan_end_at = subscription.scheduled_end_at if subscription is not None else None
    renewal_date = (
        subscription.current_period_end.date()
        if subscription_keeps_operator and plan_end_at is None and subscription.current_period_end is not None
        else None
    )
    plan_end_date = plan_end_at.date() if plan_end_at is not None else None
    operator_price = plans.PLANS[plans.OPERATOR].price_usd_month
    if operator_price is None:
        raise RuntimeError("the Operator plan must have a monthly USD price")
    team_agent_price = plans.PLANS[plans.TEAM].price_usd_agent_month
    if team_agent_price is None:
        raise RuntimeError("the Team plan must have a per-agent monthly USD price")

    return BillingPageContext(
        plan_key=organization.plan,
        plan_name=organization.plan.title(),
        # Customer-cloud plans bring their own model, so no credits exist to show;
        # a comped grant through plan_overrides brings the numbers back.
        show_credits=effective_plan.monthly_credit_grant > 0,
        credits_remaining=max(credits_remaining, 0),
        monthly_grant=effective_plan.monthly_credit_grant,
        trial_runtime_days=effective_plan.trial_runtime_days,
        max_agents=effective_plan.max_agents,
        current_period_burn=int(abs(charge_total)),
        renewal_date=renewal_date,
        plan_end_date=plan_end_date,
        show_upgrade=organization.plan == plans.TRIAL,
        show_manage_billing=subscription is not None and bool(subscription.stripe_customer_id),
        stripe_configured=bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_OPERATOR_PRICE_ID),
        operator_price_usd=str(operator_price),
        team_agent_price_usd=str(team_agent_price),
    )
