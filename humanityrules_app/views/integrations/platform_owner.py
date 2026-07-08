"""Designate the platform-owner org — the only org allowed to provision platform shares.

A platform-shared credential (``PlatformSharedCredential``) is global and
ABAC-free: HumR hands it to every customer org as the lowest-priority fallback.
Creating one is therefore a vendor action, not a customer one. The deployment
names the owning org by slug in ``settings.HUMR_PLATFORM_OWNER_ORG_SLUG`` (mirrors
the ``HUMR_SANDBOX_*`` settings pattern); this module is the single chokepoint
that turns that setting into a per-request gate. UI hiding is not enough — every
``scope=platform`` write must call ``is_platform_owner_org`` server-side.
"""

from django.conf import settings

from humanityrules_app.models import Environment, Organization

SANDBOX_APPROVAL_BLOCKED_MESSAGE = (
    "Permission changes on the shared Humanity Rules sandbox require approval "
    "by the Humanity Rules team. Your request stays saved as a draft."
)


def is_platform_owner_org(organization: Organization) -> bool:
    """Whether *organization* is the deployment's designated platform-owner org."""
    owner_slug = settings.HUMR_PLATFORM_OWNER_ORG_SLUG
    return bool(owner_slug) and organization.slug == owner_slug


def is_sandbox_approval_gated(environment: Environment) -> bool:
    """Whether permission approvals on *environment* are reserved for the platform owner.

    Environments on HumR's shared sandbox account grant IAM permissions inside HumR's
    own AWS account, so ABAC role alone cannot authorize the approval — every signup
    is admin of their own org. Only the platform-owner org may approve there.
    """
    return environment.aws_account.is_humr_sandbox and not is_platform_owner_org(organization=environment.aws_account.organization)
