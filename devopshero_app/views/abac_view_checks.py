"""
ABAC view enforcement helpers.

These are called after fetching the resource via get_object_or_404.
"""

from django.http import HttpRequest, HttpResponse, HttpResponseForbidden

from ..models import App, Environment, Workspace
from ..services import abac


def check_abac(
    request: HttpRequest,
    resource: App | Environment | Workspace,
    resource_type: str,
    action: str,
) -> HttpResponse | None:
    """
    Returns None if the user has the action on the resource,
    or HttpResponseForbidden if denied.
    """
    org = request.user.current_organization
    if not abac.check_action(org, request.user, resource, resource_type, action):
        return HttpResponseForbidden("You do not have permission to perform this action.")
    return None


def check_abac_create(request: HttpRequest, resource_type: str, action: str) -> HttpResponse | None:
    """
    For create actions where no resource exists yet.
    Evaluates with empty tags (only wildcard-resource policies match).
    """
    org = request.user.current_organization
    allowed = abac.evaluate_policies_unscoped(org, request.user, resource_type)
    if action not in allowed:
        return HttpResponseForbidden("You do not have permission to perform this action.")
    return None


def require_org_admin(request: HttpRequest) -> HttpResponse | None:
    """
    Returns None if the user is an org admin, or HttpResponseForbidden if not.
    """
    org = request.user.current_organization
    if not abac.is_org_admin(org, request.user):
        return HttpResponseForbidden("You must be an organization admin to access this page.")
    return None
