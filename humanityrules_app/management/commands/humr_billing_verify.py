"""
Verify the billing invariants: every organization's credit balance equals the sum of its
ledger entries, and no closed day's charge entry has mutated since its posting day.

Usage:
    uv run manage.py humr_billing_verify [--org SLUG]

For production, use ./prod_manage.sh humr_billing_verify instead.

The balance row is the ledger's transactional projection and the row every
credit write locks, so any drift means a write path skipped the lock or the
idempotency key, and reconciliation has to happen by hand. Past days' charges
are immutable by construction — rating derives its posting date inside the
transaction and can only address today's entry — so a closed-day mutation means
that rule broke. This command prints one line per organization checked, reports
every violation, and exits nonzero on any — so it can gate a deploy or run from
cron.

Examples:
    uv run manage.py humr_billing_verify
    uv run manage.py humr_billing_verify --org acme
"""

import datetime
from argparse import ArgumentParser
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum

from humanityrules_app import models

# A rating transaction derives its posting date from one clock read but auto_now
# stamps the later save instant, so a pass straddling midnight legitimately
# saves moments into the next day.
_DAY_CHARGE_MUTATION_GRACE = datetime.timedelta(minutes=5)


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
    help = "Check that each organization's credit balance equals the sum of its ledger entries"

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
        for organization in organizations:
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
