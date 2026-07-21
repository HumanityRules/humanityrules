"""Helpers for the shared Humanity Rules sandbox account.

Every org's sandbox environment uses the fixed slug "sandbox", so all of them resolve to
one shared base infra (humr-sandbox-*) and app resources are named humr-sandbox-{app.slug}-*.
That makes app slugs a single, global, first-come namespace across every org's sandbox.
"""

import uuid

from django.conf import settings

from humanityrules_app.models import AWSAccount, Deployment, Environment, Organization, SandboxSlugClaim

SANDBOX_ACCOUNT_NAME = "Humanity Rules Sandbox"

# Fixed env slug for every org's shared sandbox environment. All sandbox environments
# use this slug so they resolve to the one shared base infra (humr-sandbox-*).
HUMR_SANDBOX_ENV_SLUG = "sandbox"

# The node-packing rollout switch for the shared sandbox. True asserts the sandbox
# account has ECS awsvpcTrunking enabled; ensure_org_sandbox enforces it uniformly on
# every org's sandbox env row (they all share one cluster, so they must agree), and
# humr_bootstrap_sandbox passes it to deploy_base for the instance type.
SANDBOX_ENI_TRUNKING_ENABLED = True


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
    environment, _ = Environment.objects.get_or_create(
        aws_account=aws_account,
        slug=HUMR_SANDBOX_ENV_SLUG,
        defaults={
            "name": "Sandbox",
            "aws_region": settings.HUMR_SANDBOX_REGION,
            "shared_alb_hosted_zone": settings.HUMR_SANDBOX_HOSTED_ZONE,
            "status": Environment.Status.READY,
            "status_message": "Shared Humanity Rules sandbox; not provisioned per-org.",
            "eni_trunking_enabled": SANDBOX_ENI_TRUNKING_ENABLED,
        },
    )
    if environment.eni_trunking_enabled != SANDBOX_ENI_TRUNKING_ENABLED:
        environment.eni_trunking_enabled = SANDBOX_ENI_TRUNKING_ENABLED
        environment.save(update_fields=["eni_trunking_enabled"])
    return aws_account


def _sandbox_slug_taken_message(app_slug: str) -> str:
    return (
        f"The app name '{app_slug}' is already taken in the shared Humanity Rules sandbox. "
        "Rename your app and try again."
    )


async def aclaim_sandbox_app_slug(app_slug: str, organization_id: uuid.UUID, environment: Environment) -> None:
    """Reserve app_slug in the shared sandbox; raise ValueError if another org already holds it.

    Two layers, because the slug is one global namespace across every org's sandbox. First a
    friendly pre-check against committed deploys — it produces a helpful message in the common
    case and covers apps that predate the claim table (no backfill needed). Then an atomic
    SandboxSlugClaim row whose UNIQUE(slug) closes the check-then-create race that two concurrent
    first-time deploys (neither with a committed Deployment yet) would otherwise slip through.

    No-op outside the shared sandbox — dedicated customer accounts have a private AWS account per
    org and may reuse a slug across orgs. Released on app removal (see release_sandbox_app_slug).
    """
    is_sandbox = await AWSAccount.objects.filter(
        id=environment.aws_account_id, is_humr_sandbox=True,
    ).aexists()
    if not is_sandbox:
        return
    conflict = await (
        Deployment.objects
        .filter(app__environment__aws_account__is_humr_sandbox=True, app__slug=app_slug)
        .exclude(app__organization_id=organization_id)
        .aexists()
    )
    if conflict:
        raise ValueError(_sandbox_slug_taken_message(app_slug))
    # aget_or_create absorbs the racing INSERT: the loser's UNIQUE(slug) violation is caught
    # internally and re-fetched, surfacing here as created=False with the winner's org.
    claim, created = await SandboxSlugClaim.objects.aget_or_create(
        slug=app_slug,
        defaults={"organization_id": organization_id},
    )
    if not created and claim.organization_id != organization_id:
        raise ValueError(_sandbox_slug_taken_message(app_slug))


def release_sandbox_app_slug(app_slug: str, organization_id: uuid.UUID) -> None:
    """Free a shared-sandbox slug claim on app removal so the name can be reused. No-op if unclaimed."""
    SandboxSlugClaim.objects.filter(slug=app_slug, organization_id=organization_id).delete()
