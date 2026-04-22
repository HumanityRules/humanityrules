"""
Shared resolver for management commands that target a customer AWS account.

Commands take `--account [--org] [--env]` args and need:
  1. AWSAccount lookup (by name or 12-digit id, optionally scoped to an org)
  2. Environment lookup within that account (default slug "default")
  3. An assumed-role boto3 session against that account

Extracted from the near-identical copies in doh_app_logs.py, doh_app_shell.py,
and doh_efs_browse.py. New commands should route through here rather than
duplicate the shape; existing commands migrate opportunistically.
"""

import argparse
from dataclasses import dataclass

import boto3
from django.conf import settings
from django.core.management.base import CommandError

from devopshero_app.models import AWSAccount, Environment, Organization
from devopshero_app.services.infra_customer import iam_utils


@dataclass
class ResolvedAwsTarget:
    """Result of resolving --account/--org/--env into concrete rows + a boto3 session."""
    aws_account: AWSAccount
    environment: Environment
    session: boto3.Session


def add_aws_target_args(parser: argparse.ArgumentParser, env_default: str = "default") -> None:
    """Attach the shared --account / --org / --env arguments to a parser."""
    parser.add_argument("--account", required=True, help="AWS account name or 12-digit account ID")
    parser.add_argument("--org", help="Organization name or slug (required when account name is ambiguous across orgs)")
    parser.add_argument("--env", default=env_default, help=f"Environment slug (default: {env_default!r})")


def get_aws_account(identifier: str, org_slug: str | None) -> AWSAccount:
    """Look up an AWSAccount by name or 12-digit ID, optionally scoped to an org."""
    qs = AWSAccount.objects.all()

    if org_slug:
        org = Organization.objects.filter(slug=org_slug).first() or Organization.objects.filter(name=org_slug).first()
        if not org:
            raise CommandError(f"No organization matching '{org_slug}' found (tried slug and name).")
        qs = qs.filter(organization=org)

    if identifier.isdigit() and len(identifier) == 12:
        try:
            return qs.get(aws_account_id=identifier)
        except AWSAccount.DoesNotExist:
            raise CommandError(f"AWS account with ID '{identifier}' not found")
        except AWSAccount.MultipleObjectsReturned:
            raise CommandError(f"Multiple accounts with ID '{identifier}'. Use --org to disambiguate.")
    try:
        return qs.get(name=identifier)
    except AWSAccount.DoesNotExist:
        raise CommandError(f"AWS account '{identifier}' not found")
    except AWSAccount.MultipleObjectsReturned:
        raise CommandError(
            f"Multiple accounts named '{identifier}'. Use --org or the 12-digit account ID to disambiguate."
        )


def resolve_aws_target(account: str, org: str | None, env: str) -> ResolvedAwsTarget:
    """Resolve --account/--org/--env into an AWSAccount, Environment, and assumed-role boto3 session."""
    access_key = settings.DOH_AWS_ACCESS_KEY
    secret_key = settings.DOH_AWS_SECRET_KEY
    if not access_key or not secret_key:
        raise CommandError("Missing DOH_AWS_ACCESS_KEY and/or DOH_AWS_SECRET_KEY in .env")

    aws_account = get_aws_account(identifier=account, org_slug=org)

    try:
        environment = Environment.objects.get(aws_account=aws_account, slug=env)
    except Environment.DoesNotExist:
        raise CommandError(f"No environment with slug '{env}' for AWS account '{aws_account.name}'.")

    session = iam_utils.get_assumed_role_session(
        access_key=access_key,
        secret_key=secret_key,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=environment.aws_region,
    )

    return ResolvedAwsTarget(aws_account=aws_account, environment=environment, session=session)
