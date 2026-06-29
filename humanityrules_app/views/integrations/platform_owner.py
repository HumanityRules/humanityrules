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

from humanityrules_app.models import Organization


def is_platform_owner_org(organization: Organization) -> bool:
    """Whether *organization* is the deployment's designated platform-owner org."""
    owner_slug = settings.HUMR_PLATFORM_OWNER_ORG_SLUG
    return bool(owner_slug) and organization.slug == owner_slug
