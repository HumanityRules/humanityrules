"""
Shared resolver for management commands that target a customer AWS account.

Two modes:

- **DB mode** (default): operator's local Django DB is the source of truth.
  Args: `--account [--org] --env`.
  Looks up the AWSAccount and Environment rows, then assumes the cross-account
  role using their stored `aws_account_id` + `external_id` + `aws_region`.

- **Raw mode** (DB-less): all four target values are passed explicitly.
  Args: `--aws-account-id --aws-external-id --aws-region --env-slug`.
  Skips the local Django DB entirely. Used when the destination env lives in a
  different DB than the local one (typical case: operator on a laptop pushing
  into a prod-managed env). The four values are normally fetched from prod's
  DB by the prod_manage.sh dispatcher and injected here.

The two modes are mutually exclusive — pass one set or the other, not both.

Commands that need `aws_account.organization` (e.g. `doh_app_*` for App lookup)
are inherently DB-mode-only: in raw mode `target.aws_account` and
`target.environment` are None.
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
    """Result of resolving target args into the four AWS values + a boto3 session."""
    aws_account_id: str
    external_id: str
    aws_region: str
    env_slug: str
    session: boto3.Session
    aws_account: AWSAccount | None
    environment: Environment | None


def add_aws_target_args(parser: argparse.ArgumentParser, env_default: str | None) -> None:
    """Attach DB-mode and raw-mode target args to a parser.

    DB mode:  --account [--org] --env
    Raw mode: --aws-account-id --aws-external-id --aws-region --env-slug
    """
    parser.add_argument("--account", help="AWS account name or 12-digit account ID (DB mode)")
    parser.add_argument("--org", help="Organization name or slug (DB mode, required when account name is ambiguous across orgs)")
    if env_default is None:
        parser.add_argument("--env", help="Environment slug (DB mode)")
    else:
        parser.add_argument("--env", default=env_default, help=f"Environment slug (DB mode, default: {env_default!r})")

    parser.add_argument("--aws-account-id", dest="aws_account_id", help="12-digit AWS account ID (raw mode; bypasses local DB)")
    parser.add_argument("--aws-external-id", dest="aws_external_id", help="AssumeRole ExternalId UUID (raw mode; bypasses local DB)")
    parser.add_argument("--aws-region", dest="aws_region", help="AWS region (raw mode; bypasses local DB)")
    parser.add_argument("--env-slug", dest="env_slug", help="Environment slug for ECR path (raw mode; bypasses local DB)")


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


_RAW_MODE_ARG_KEYS = ("aws_account_id", "aws_external_id", "aws_region", "env_slug")
_DB_MODE_ARG_KEYS = ("account", "org", "env")


def resolve_aws_target(options: dict) -> ResolvedAwsTarget:
    """Resolve argparse options (DB mode or raw mode) into a ResolvedAwsTarget."""
    raw_set = {k: options.get(k) for k in _RAW_MODE_ARG_KEYS if options.get(k) is not None}
    db_set = {k: options.get(k) for k in _DB_MODE_ARG_KEYS if options.get(k) is not None}

    if raw_set and any(k in db_set for k in ("account", "org")):
        raise CommandError(
            "Cannot mix DB-mode args (--account/--org) with raw-mode args "
            "(--aws-account-id/--aws-external-id/--aws-region/--env-slug). Pick one mode."
        )

    if raw_set:
        missing = [k for k in _RAW_MODE_ARG_KEYS if not options.get(k)]
        if missing:
            flag_names = {
                "aws_account_id": "--aws-account-id",
                "aws_external_id": "--aws-external-id",
                "aws_region": "--aws-region",
                "env_slug": "--env-slug",
            }
            raise CommandError(f"Raw mode requires all of: {', '.join(flag_names[k] for k in missing)}")
        return _resolve_raw(
            aws_account_id=options["aws_account_id"],
            external_id=options["aws_external_id"],
            aws_region=options["aws_region"],
            env_slug=options["env_slug"],
        )

    if not options.get("account"):
        raise CommandError(
            "Must provide either --account (DB mode) or all of "
            "--aws-account-id/--aws-external-id/--aws-region/--env-slug (raw mode)."
        )

    return _resolve_db(account=options["account"], org=options.get("org"), env=options.get("env"))


def _build_session(aws_account_id: str, external_id: str, region: str) -> boto3.Session:
    """Build an assumed-role boto3 session for the given account/region."""
    access_key = settings.DOH_AWS_ACCESS_KEY
    secret_key = settings.DOH_AWS_SECRET_KEY
    if not access_key or not secret_key:
        # Prod ECS tasks have no .env; use the task role (see app_stack task_role).
        access_key = None
        secret_key = None
    return iam_utils.get_assumed_role_session(
        access_key=access_key,
        secret_key=secret_key,
        account_id=aws_account_id,
        external_id=external_id,
        region=region,
    )


def _resolve_db(account: str, org: str | None, env: str | None) -> ResolvedAwsTarget:
    """DB-mode resolution: look up rows in the local Django DB."""
    if not env:
        raise CommandError("DB mode requires --env (environment slug).")
    aws_account = get_aws_account(identifier=account, org_slug=org)
    try:
        environment = Environment.objects.get(aws_account=aws_account, slug=env)
    except Environment.DoesNotExist:
        raise CommandError(f"No environment with slug '{env}' for AWS account '{aws_account.name}'.")

    session = _build_session(
        aws_account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=environment.aws_region,
    )

    return ResolvedAwsTarget(
        aws_account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        aws_region=environment.aws_region,
        env_slug=environment.slug,
        session=session,
        aws_account=aws_account,
        environment=environment,
    )


def _resolve_raw(aws_account_id: str, external_id: str, aws_region: str, env_slug: str) -> ResolvedAwsTarget:
    """Raw-mode resolution: build the session directly from passed-in values; skip DB."""
    if not (aws_account_id.isdigit() and len(aws_account_id) == 12):
        raise CommandError(f"--aws-account-id must be a 12-digit AWS account ID (got {aws_account_id!r}).")
    session = _build_session(
        aws_account_id=aws_account_id,
        external_id=external_id,
        region=aws_region,
    )
    return ResolvedAwsTarget(
        aws_account_id=aws_account_id,
        external_id=external_id,
        aws_region=aws_region,
        env_slug=env_slug,
        session=session,
        aws_account=None,
        environment=None,
    )
