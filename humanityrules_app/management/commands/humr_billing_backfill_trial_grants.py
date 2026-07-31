"""
Give every organization that predates billing its one-time trial credit grant.

Usage:
    uv run manage.py humr_billing_backfill_trial_grants [--org SLUG]

For production, use ./prod_manage.sh humr_billing_backfill_trial_grants instead.

Run once after migrating to the plan fields. Safe to re-run and safe to race the
signup path: the grant is written through the same service function with the
same idempotency key, ``grant:trial:{org_id}``, so an organization that already
has one is skipped rather than granted twice.

This is a command rather than a data migration because a credit movement has to
go through the balance row's SELECT FOR UPDATE and the ledger's idempotency key,
and that path is real-model code. Reimplementing it against a migration's
historical models would mean a second copy of the money logic.

Examples:
    uv run manage.py humr_billing_backfill_trial_grants
    uv run manage.py humr_billing_backfill_trial_grants --org acme
"""

from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError

from humanityrules_app import models
from humanityrules_app.services.billing import grants


class Command(BaseCommand):
    help = "Write the one-time trial credit grant for every organization that has none"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--org", dest="org_slug", help="Limit the backfill to one organization slug")

    def handle(self, *args: object, **options: object) -> None:
        org_slug = options["org_slug"]
        organizations = models.Organization.objects.order_by("slug")
        if org_slug:
            organizations = organizations.filter(slug=org_slug)
            if not organizations.exists():
                raise CommandError(f"No organization with slug {org_slug!r}")

        granted = 0
        skipped = 0
        for organization in organizations:
            if grants.grant_trial_credits(organization=organization):
                granted += 1
                self.stdout.write(f"{organization.slug}: trial grant written")
            else:
                skipped += 1
                self.stdout.write(f"{organization.slug}: already granted")

        self.stdout.write(self.style.SUCCESS(
            f"Trial grants: {granted} written, {skipped} already present"
        ))
