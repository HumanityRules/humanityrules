"""Resolve the org-shared integration credential a (user, app) pair should receive.

Shared credentials are admin-provisioned and override the user's own pasted key
(organization wins). Authorization is ABAC: the org's
``IntegrationSharedCredential`` rows for the provider are filtered through
``credential:use`` — supplying the requesting app as ``$app`` context — then the
most specific match wins (user > workspace > everyone).
"""

import logging

from humanityrules_app.models import App, IntegrationSharedCredential, Organization, User
from humanityrules_app.services import abac_service

logger = logging.getLogger(__name__)

# Most-specific scope wins when several shared credentials match one request.
# The model's uniqueness constraints plus ABAC filtering guarantee at most one
# permitted credential per scope, so this is an unambiguous pick.
_SCOPE_PRECEDENCE = (
    IntegrationSharedCredential.Scope.USER,
    IntegrationSharedCredential.Scope.WORKSPACE,
    IntegrationSharedCredential.Scope.EVERYONE,
)


def resolve(organization: Organization, user: User, app: App | None, provider: str) -> IntegrationSharedCredential | None:
    """Return the shared credential this (user, app) should use for *provider*, or None."""
    candidates = IntegrationSharedCredential.objects.filter(organization=organization, provider=provider)
    permitted = abac_service.filter_permitted_credentials(
        organization=organization, user=user, app=app, queryset=candidates,
    )
    if not permitted:
        return None
    by_scope = {credential.scope: credential for credential in permitted}
    for scope in _SCOPE_PRECEDENCE:
        if scope in by_scope:
            return by_scope[scope]
    return None
