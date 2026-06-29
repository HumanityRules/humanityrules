"""Resolve the platform-shared integration credential a provider falls back to.

Platform credentials are HumR-provisioned and shared with every customer org.
They sit at the bottom of the resolution ladder (``org-shared > personal >
platform``): the broker uses one only where neither an org-shared nor a personal
credential carries a usable secret. Resolution is global and ABAC-free — a model
uniqueness constraint guarantees at most one enabled row per provider, so this is
an unambiguous pick.
"""

import logging

from humanityrules_app.models import PlatformSharedCredential

logger = logging.getLogger(__name__)


def resolve(provider: str) -> PlatformSharedCredential | None:
    """Return the enabled platform credential for *provider*, or None."""
    return PlatformSharedCredential.objects.filter(provider=provider, enabled=True).first()
