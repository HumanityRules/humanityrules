"""
List or permanently purge customer Secrets Manager secrets (cross-account),
and manage shared environment secrets.

Usage:
    uv run manage.py doh_secrets list --account "Humanity Rules Sandbox"
    uv run manage.py doh_secrets list --account "Humanity Rules Sandbox" --include-deleted
    uv run manage.py doh_secrets purge-deleted --account "Humanity Rules Sandbox" --dry-run
    uv run manage.py doh_secrets purge-deleted --account "Humanity Rules Sandbox"

    uv run manage.py doh_secrets shared-list --account "Humanity Rules Sandbox" --env default
    uv run manage.py doh_secrets shared-set --account "Humanity Rules Sandbox" --env default OPENAI_API_KEY=sk-xxx TAVILY_API_KEY=tvly-xxx
    uv run manage.py doh_secrets shared-delete --account "Humanity Rules Sandbox" --env default OPENAI_API_KEY TAVILY_API_KEY

All subcommands accept --org <slug> to disambiguate when multiple organizations
share the same account name.

`purge-deleted` calls DeleteSecret with ForceDeleteWithoutRecovery on secrets that
are already scheduled for deletion (skips the recovery window).

`shared-*` subcommands manage the per-environment shared secrets store
(devopshero/{env-slug}/shared-secrets). Values set here are automatically
used as defaults for empty-placeholder secrets when deploying apps.

Requires DOH_AWS_ACCESS_KEY and DOH_AWS_SECRET_KEY (via Django settings).

For production: ./prod_manage.sh doh_secrets <subcommand> ...
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import AWSAccount, Environment, Organization
from devopshero_app.services.infra_customer import iam_utils
from devopshero_app.services.infra_customer import secrets_utils

DEFAULT_REGION = "us-east-1"


class Command(BaseCommand):
    help = "Manage customer Secrets Manager secrets (list, purge, shared environment secrets)"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="operation", required=True)

        list_cmd = subparsers.add_parser("list", help="List secrets in Secrets Manager")
        _add_account_args(list_cmd)
        list_cmd.add_argument(
            "--include-deleted",
            action="store_true",
            help="Include secrets scheduled for deletion",
        )

        purge_cmd = subparsers.add_parser(
            "purge-deleted",
            help="Permanently delete secrets already scheduled for deletion",
        )
        _add_account_args(purge_cmd)
        purge_cmd.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be purged without deleting",
        )

        shared_list_cmd = subparsers.add_parser("shared-list", help="List key names in environment shared secrets")
        _add_account_args(shared_list_cmd)
        shared_list_cmd.add_argument("--env", required=True, help="Environment slug (e.g. default, prod)")
        shared_list_cmd.add_argument("--reveal", action="store_true", help="Show secret values instead of masking them")

        shared_set_cmd = subparsers.add_parser("shared-set", help="Set keys in environment shared secrets (KEY=VALUE ...)")
        _add_account_args(shared_set_cmd)
        shared_set_cmd.add_argument("--env", required=True, help="Environment slug (e.g. default, prod)")
        shared_set_cmd.add_argument("pairs", nargs="+", metavar="KEY=VALUE", help="Key-value pairs to set")

        shared_del_cmd = subparsers.add_parser("shared-delete", help="Delete keys from environment shared secrets")
        _add_account_args(shared_del_cmd)
        shared_del_cmd.add_argument("--env", required=True, help="Environment slug (e.g. default, prod)")
        shared_del_cmd.add_argument("keys", nargs="+", metavar="KEY", help="Key names to remove")

    def handle(self, *args, **options):
        access_key = settings.DOH_AWS_ACCESS_KEY
        secret_key = settings.DOH_AWS_SECRET_KEY
        if not access_key or not secret_key:
            raise CommandError("Missing DOH_AWS_ACCESS_KEY and/or DOH_AWS_SECRET_KEY in environment")

        operation = options["operation"]
        aws_account = _get_connected_aws_account(identifier=options["account"], org_slug=options.get("org"))

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
        elif operation == "shared-list":
            env = _get_environment(aws_account=aws_account, env_slug=options["env"])
            self._run_shared_list(session=session, env_slug=env.slug, reveal=options["reveal"])
        elif operation == "shared-set":
            env = _get_environment(aws_account=aws_account, env_slug=options["env"])
            self._run_shared_set(session=session, env_slug=env.slug, pairs=options["pairs"])
        elif operation == "shared-delete":
            env = _get_environment(aws_account=aws_account, env_slug=options["env"])
            self._run_shared_delete(session=session, env_slug=env.slug, keys=options["keys"])

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

    def _run_shared_list(self, session, env_slug: str, reveal: bool) -> None:
        shared = secrets_utils.get_shared_secrets(session=session, env_slug=env_slug)
        secret_name = f"devopshero/{env_slug}/shared-secrets"

        if not shared:
            self.stdout.write(f"No shared secrets found for environment '{env_slug}'.")
            self.stdout.write(f"   (looked for '{secret_name}' in Secrets Manager)")
            return

        self.stdout.write(f"=== Shared secrets for '{env_slug}' ({len(shared)} key(s)) ===")
        self.stdout.write(f"   Secret: {secret_name}")
        self.stdout.write("")
        for key in sorted(shared):
            value = shared[key] if reveal else _mask_value(shared[key])
            self.stdout.write(f"   {key} = {value}")
        self.stdout.write("")

    def _run_shared_set(self, session, env_slug: str, pairs: list[str]) -> None:
        new_values = {}
        for pair in pairs:
            if "=" not in pair:
                raise CommandError(f"Invalid format '{pair}'. Expected KEY=VALUE.")
            key, value = pair.split("=", 1)
            key = key.strip()
            if not key:
                raise CommandError(f"Empty key in '{pair}'.")
            new_values[key] = value

        secret_name = f"devopshero/{env_slug}/shared-secrets"
        sm_client = session.client("secretsmanager")

        shared = secrets_utils.get_shared_secrets(session=session, env_slug=env_slug)
        if shared:
            shared.update(new_values)
            sm_client.put_secret_value(SecretId=secret_name, SecretString=json.dumps(shared))
        else:
            sm_client.create_secret(
                Name=secret_name,
                Description=f"Shared secrets for environment '{env_slug}'",
                SecretString=json.dumps(new_values),
            )

        for key in sorted(new_values):
            self.stdout.write(f"   ✅ {key} = {_mask_value(new_values[key])}")
        self.stdout.write(self.style.SUCCESS(f"Set {len(new_values)} key(s) in '{secret_name}'."))

    def _run_shared_delete(self, session, env_slug: str, keys: list[str]) -> None:
        secret_name = f"devopshero/{env_slug}/shared-secrets"

        shared = secrets_utils.get_shared_secrets(session=session, env_slug=env_slug)
        if not shared:
            raise CommandError(f"No shared secrets found for environment '{env_slug}'.")

        removed = []
        missing = []
        for key in keys:
            if key in shared:
                del shared[key]
                removed.append(key)
            else:
                missing.append(key)

        if missing:
            self.stderr.write(f"   Key(s) not found: {', '.join(sorted(missing))}")

        if removed:
            sm_client = session.client("secretsmanager")
            sm_client.put_secret_value(SecretId=secret_name, SecretString=json.dumps(shared))
            for key in sorted(removed):
                self.stdout.write(f"   🗑️  {key}")
            self.stdout.write(self.style.SUCCESS(f"Removed {len(removed)} key(s) from '{secret_name}'."))


def _add_account_args(subparser) -> None:
    """Add --account and --org arguments to a subparser."""
    subparser.add_argument("--account", required=True, help="Connected AWS account name or 12-digit account ID")
    subparser.add_argument("--org", help="Organization name or slug (required when account name is ambiguous across orgs)")


def _mask_value(value: str) -> str:
    """Mask a secret value, showing only the first 4 characters."""
    if len(value) <= 4:
        return "****"
    return value[:4] + "****"


def _get_environment(aws_account: AWSAccount, env_slug: str) -> Environment:
    """Resolve an Environment by slug within the given AWS account."""
    try:
        return Environment.objects.get(aws_account=aws_account, slug=env_slug)
    except Environment.DoesNotExist:
        available = list(Environment.objects.filter(aws_account=aws_account).values_list("slug", flat=True))
        available_str = ", ".join(available) if available else "(none)"
        raise CommandError(f"No environment '{env_slug}' in account '{aws_account.name}'. Available: {available_str}")


def _get_connected_aws_account(identifier: str, org_slug: str | None) -> AWSAccount:
    """Resolve a connected AWSAccount by name or 12-digit account ID, optionally scoped to an org."""
    qs = AWSAccount.objects.filter(status=AWSAccount.Status.CONNECTED)

    if org_slug:
        org = Organization.objects.filter(slug=org_slug).first() or Organization.objects.filter(name=org_slug).first()
        if not org:
            raise CommandError(f"No organization matching '{org_slug}' found (tried slug and name).")
        qs = qs.filter(organization=org)

    if not qs.exists():
        raise CommandError("No connected AWS accounts found.")

    if identifier.isdigit() and len(identifier) == 12:
        try:
            return qs.get(aws_account_id=identifier)
        except AWSAccount.DoesNotExist:
            raise CommandError(f"No connected AWS account with ID '{identifier}' found.")
        except AWSAccount.MultipleObjectsReturned:
            raise CommandError(
                f"Multiple connected accounts with ID '{identifier}'. Use --org to disambiguate.",
            )

    try:
        return qs.get(name=identifier)
    except AWSAccount.DoesNotExist:
        raise CommandError(f"No connected AWS account named '{identifier}' found.")
    except AWSAccount.MultipleObjectsReturned:
        raise CommandError(
            f"Multiple connected accounts named '{identifier}'. Use --org or the 12-digit account ID to disambiguate.",
        )
