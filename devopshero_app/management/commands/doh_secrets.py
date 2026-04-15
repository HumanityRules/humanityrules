"""
List or permanently purge customer Secrets Manager secrets (cross-account).

Usage:
    uv run manage.py doh_secrets list --account "CH Sandbox"
    uv run manage.py doh_secrets list --account "CH Sandbox" --include-deleted
    uv run manage.py doh_secrets purge-deleted --account "CH Sandbox" --dry-run
    uv run manage.py doh_secrets purge-deleted --account "CH Sandbox"

`purge-deleted` calls DeleteSecret with ForceDeleteWithoutRecovery on secrets that
are already scheduled for deletion (skips the recovery window).

Requires DOH_AWS_ACCESS_KEY and DOH_AWS_SECRET_KEY (via Django settings).

For production: ./prod_manage.sh doh_secrets <subcommand> ...
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import AWSAccount
from devopshero_app.services.infra_customer import iam_utils
from devopshero_app.services.infra_customer import secrets_utils

DEFAULT_REGION = "us-east-1"


class Command(BaseCommand):
    help = "List or purge pending-deletion secrets in a customer AWS account (Secrets Manager)"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="operation", required=True)

        list_cmd = subparsers.add_parser("list", help="List secrets in Secrets Manager")
        list_cmd.add_argument("--account", required=True, help="Connected AWS account name or 12-digit account ID")
        list_cmd.add_argument(
            "--include-deleted",
            action="store_true",
            help="Include secrets scheduled for deletion",
        )

        purge_cmd = subparsers.add_parser(
            "purge-deleted",
            help="Permanently delete secrets already scheduled for deletion",
        )
        purge_cmd.add_argument("--account", required=True, help="Connected AWS account name or 12-digit account ID")
        purge_cmd.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be purged without deleting",
        )

    def handle(self, *args, **options):
        access_key = settings.DOH_AWS_ACCESS_KEY
        secret_key = settings.DOH_AWS_SECRET_KEY
        if not access_key or not secret_key:
            raise CommandError("Missing DOH_AWS_ACCESS_KEY and/or DOH_AWS_SECRET_KEY in environment")

        operation = options["operation"]
        aws_account = _get_connected_aws_account(identifier=options["account"])

        self.stdout.write(f"Using AWS account: {aws_account.name} ({aws_account.aws_account_id})")
        self.stdout.write("")

        session = iam_utils.get_assumed_role_session(
            access_key=access_key,
            secret_key=secret_key,
            account_id=aws_account.aws_account_id,
            external_id=str(aws_account.external_id),
            region=DEFAULT_REGION,
        )

        if operation == "list":
            self._run_list(session=session, include_deleted=options["include_deleted"])
        elif operation == "purge-deleted":
            secrets_utils.purge_deleted_secrets(session=session, dry_run=options["dry_run"])

    def _run_list(self, session, include_deleted: bool) -> None:
        secrets_list = secrets_utils.list_secrets(session=session, include_deleted=include_deleted)

        if not secrets_list:
            self.stdout.write("No secrets found.")
            return

        self.stdout.write(f"=== Secrets ({len(secrets_list)}) ===")
        self.stdout.write("")
        for secret in secrets_list:
            status = "🗑️ " if secret["deleted_date"] else "📌"
            self.stdout.write(f"{status} {secret['name']}")
            if secret["description"]:
                self.stdout.write(f"   Description: {secret['description']}")
            self.stdout.write(f"   ARN: {secret['arn']}")
            if secret["deleted_date"]:
                self.stdout.write(f"   Scheduled deletion: {secret['deleted_date']}")
            self.stdout.write("")


def _get_connected_aws_account(identifier: str) -> AWSAccount:
    """Resolve a connected AWSAccount by name or 12-digit account ID."""
    qs = AWSAccount.objects.filter(status=AWSAccount.Status.CONNECTED)
    if not qs.exists():
        raise CommandError("No connected AWS accounts in the database.")

    if identifier.isdigit() and len(identifier) == 12:
        try:
            return qs.get(aws_account_id=identifier)
        except AWSAccount.DoesNotExist:
            raise CommandError(f"No connected AWS account with ID '{identifier}' found.")

    try:
        return qs.get(name=identifier)
    except AWSAccount.DoesNotExist:
        raise CommandError(f"No connected AWS account named '{identifier}' found.")
    except AWSAccount.MultipleObjectsReturned:
        raise CommandError(
            f"Multiple connected accounts named '{identifier}'. Use the 12-digit account ID instead.",
        )
