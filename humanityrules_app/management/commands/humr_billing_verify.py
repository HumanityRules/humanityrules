"""
Verify the billing invariants: every organization's credit balance equals the sum of its
ledger entries, no closed day's charge entry has mutated since its posting day, and no
active Operator subscription is missing a payment consequence its own mirror already implies.

Usage:
    uv run manage.py humr_billing_verify [--org SLUG]

For production, use ./prod_manage.sh humr_billing_verify instead.

The balance row is the ledger's transactional projection and the row every
credit write locks, so any drift means a write path skipped the lock or the
idempotency key, and reconciliation has to happen by hand. Past days' charges
are immutable by construction — rating derives its posting date inside the
transaction and can only address today's entry — so a closed-day mutation means
that rule broke. A subscription's paid period briefly trailing its current
period is normal for about an hour around renewal, while Stripe's
``invoice.paid`` webhook catches up, so the lost-invoice check only flags the
lag once the mirror's ``updated_at`` has sat unchanged for a full day — a
conservative margin past that ordinary delay. The companion missing-grant
check needs no such grace: once the paid period already equals the current
period, reconciliation had every fact it needed to post the grant, so the
grant's absence is a violation from that moment on. Neither check has any
period history to draw on, only the mirror's present state, so the next
successful renewal overwrites the very fields each check depends on and the
violation goes invisible again — this command has no schedule of its own, so
an operator has to run it at least once per monthly billing cycle for either
check to have a chance of catching what it looks for. It is otherwise
read-only: it prints one line per organization checked, reports every
violation, and exits nonzero on any.

Examples:
    uv run manage.py humr_billing_verify
    uv run manage.py humr_billing_verify --org acme
"""

import datetime
from argparse import ArgumentParser
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum
from django.utils import timezone

from humanityrules_app import models

# A rating transaction derives its posting date from one clock read but auto_now
# stamps the later save instant, so a pass straddling midnight legitimately
# saves moments into the next day.
_DAY_CHARGE_MUTATION_GRACE = datetime.timedelta(minutes=5)
_LOST_PAID_INVOICE_GRACE = datetime.timedelta(days=1)


def _subscription_payment_violations(organization: models.Organization, now: datetime.datetime) -> list[str]:
    """Detect missing payment consequences for the organization's active Operator subscription.

    Only an active mirror supports the premise that payment should already have
    arrived: ``past_due`` means nobody paid, and ``canceled`` means there is no
    live subscription to expect payment from, even if an operator hand-set the
    organization's plan. Two independent things can be missing from there — the
    paid-period fact never arrived (the lag check, with its one-day grace; see
    the module docstring), or it arrived but the grant it should have triggered
    didn't (the grant check, no grace needed) — so this can return up to two
    violation lines for the same subscription.
    """
    if organization.plan != models.Organization.Plan.OPERATOR:
        return []
    subscription = models.BillingSubscription.objects.filter(
        organization=organization,
        status="active",
    ).first()
    if (
        subscription is None
        or subscription.current_period_start is None
        or subscription.latest_paid_period_start is None
    ):
        return []

    violations: list[str] = []
    if (
        subscription.latest_paid_period_start < subscription.current_period_start
        and subscription.updated_at < now - _LOST_PAID_INVOICE_GRACE
    ):
        violations.append(
            f"{organization.slug}: possible lost invoice.paid for active subscription "
            f"{subscription.stripe_subscription_id}: latest paid period "
            f"{subscription.latest_paid_period_start.isoformat()} is older than current period "
            f"{subscription.current_period_start.isoformat()}, and the mirror has been unchanged since "
            f"{subscription.updated_at.isoformat()}"
        )
    if subscription.latest_paid_period_start == subscription.current_period_start:
        grant_key = f"grant:{subscription.stripe_subscription_id}:{subscription.current_period_start.date().isoformat()}"
        grant_exists = models.BillingLedgerEntry.objects.filter(
            organization=organization,
            type=models.BillingLedgerEntry.Type.GRANT,
            idempotency_key=grant_key,
        ).exists()
        if not grant_exists:
            violations.append(
                f"{organization.slug}: missing Operator period grant {grant_key} for active subscription "
                f"{subscription.stripe_subscription_id} whose current period is confirmed paid"
            )
    return violations


def _closed_day_mutations(organization: models.Organization) -> list[str]:
    """Day charge entries whose last mutation happened after their posting day closed."""
    violations: list[str] = []
    day_charges = models.BillingLedgerEntry.objects.filter(
        organization=organization, type=models.BillingLedgerEntry.Type.CHARGE, idempotency_key__startswith="charge:",
    )
    for entry in day_charges:
        posting_date = datetime.date.fromisoformat(entry.idempotency_key.rsplit(":", 1)[-1])
        day_close = datetime.datetime.combine(
            posting_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=datetime.UTC,
        )
        if entry.updated_at > day_close + _DAY_CHARGE_MUTATION_GRACE:
            violations.append(
                f"{organization.slug}: day charge {entry.idempotency_key} mutated at "
                f"{entry.updated_at.isoformat()}, after its posting day closed"
            )
    return violations


class Command(BaseCommand):
    help = "Check billing ledger integrity and active-subscription payment consequences"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--org", dest="org_slug", help="Limit the check to one organization slug")

    def handle(self, *args: object, **options: object) -> None:
        org_slug = options["org_slug"]
        organizations = models.Organization.objects.order_by("slug")
        if org_slug:
            organizations = organizations.filter(slug=org_slug)
            if not organizations.exists():
                raise CommandError(f"No organization with slug {org_slug!r}")

        discrepancies: list[str] = []
        verification_started_at = timezone.now()
        for organization in organizations:
            discrepancies.extend(
                _subscription_payment_violations(organization=organization, now=verification_started_at),
            )
            discrepancies.extend(_closed_day_mutations(organization=organization))
            ledger = models.BillingLedgerEntry.objects.filter(organization=organization).aggregate(
                total=Sum("amount"), entry_count=Count("id"),
            )
            ledger_total = ledger["total"] if ledger["total"] is not None else Decimal(0)
            entry_count = ledger["entry_count"]
            balance = models.BillingBalance.objects.filter(organization=organization).first()

            if balance is None and entry_count == 0:
                self.stdout.write(f"{organization.slug}: no billing activity")
                continue
            if balance is None:
                discrepancies.append(
                    f"{organization.slug}: {entry_count} ledger entry(ies) summing to {ledger_total} but no balance row"
                )
                continue
            if balance.credits != ledger_total:
                drift = balance.credits - ledger_total
                discrepancies.append(
                    f"{organization.slug}: balance {balance.credits} != ledger sum {ledger_total} "
                    f"over {entry_count} entry(ies) (drift {drift})"
                )
                continue
            self.stdout.write(f"{organization.slug}: balance {balance.credits} matches {entry_count} ledger entry(ies)")

        if discrepancies:
            for line in discrepancies:
                self.stderr.write(line)
            raise CommandError(f"{len(discrepancies)} billing invariant violation(s)")

        self.stdout.write(self.style.SUCCESS("Billing invariants hold for every organization checked"))
