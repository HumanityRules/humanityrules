"""Helpers for the shared Humanity Rules sandbox account.

Every org's sandbox environment uses the fixed slug "sandbox", so all of them resolve to
one shared base infra (humr-sandbox-*) and app resources are named humr-sandbox-{app.slug}-*.
That makes app slugs a single, global, first-come namespace across every org's sandbox.
"""

import uuid

from django.conf import settings

from humanityrules_app.models import App, AWSAccount, Deployment, Environment, Organization

SANDBOX_ACCOUNT_NAME = "Humanity Rules Sandbox"

# Fixed env slug for every org's shared sandbox environment. All sandbox environments
# use this slug so they resolve to the one shared base infra (humr-sandbox-*).
HUMR_SANDBOX_ENV_SLUG = "sandbox"


def is_sandbox_configured() -> bool:
    """Whether this deployment offers the shared sandbox (both settings present)."""
    return bool(settings.HUMR_SANDBOX_AWS_ACCOUNT_ID and settings.HUMR_SANDBOX_EXTERNAL_ID)


def ensure_org_sandbox(organization: Organization) -> AWSAccount | None:
    """Give an org a connected sandbox AWS account + ready environment. Idempotent; no-op if unconfigured."""
    if not is_sandbox_configured():
        return None
    aws_account, _ = AWSAccount.objects.get_or_create(
        organization=organization,
        name=SANDBOX_ACCOUNT_NAME,
        defaults={
            "aws_account_id": settings.HUMR_SANDBOX_AWS_ACCOUNT_ID,
            "external_id": uuid.UUID(settings.HUMR_SANDBOX_EXTERNAL_ID),
            "status": AWSAccount.Status.CONNECTED,
            "is_humr_sandbox": True,
        },
    )
    # A ready-to-use environment pinned to the fixed sandbox slug, so it resolves to the
    # one shared base infra (humr-sandbox-*) provisioned once. No per-org provisioning.
    Environment.objects.get_or_create(
        aws_account=aws_account,
        slug=HUMR_SANDBOX_ENV_SLUG,
        defaults={
            "name": "Sandbox",
            "aws_region": settings.HUMR_SANDBOX_REGION,
            "shared_alb_hosted_zone": settings.HUMR_SANDBOX_HOSTED_ZONE,
            "status": Environment.Status.READY,
            "status_message": "Shared Humanity Rules sandbox; not provisioned per-org.",
        },
    )
    return aws_account


async def acheck_sandbox_app_name_available(app: App, environment: Environment) -> None:
    """Raise ValueError if another org already claimed this app slug in the shared sandbox."""
    if not environment.aws_account.is_humr_sandbox:
        return
    conflict = await (
        Deployment.objects
        .filter(environment__aws_account__is_humr_sandbox=True, app__slug=app.slug)
        .exclude(app__organization_id=app.organization_id)
        .aexists()
    )
    if conflict:
        raise ValueError(
            f"The app name '{app.slug}' is already taken in the shared Humanity Rules sandbox. "
            "Rename your app and try again."
        )
