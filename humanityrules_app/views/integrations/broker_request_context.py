"""Shared request plumbing for the HUMR endpoints that deployed apps call.

Every one of these requests has the same envelope: a bearer token in the
Authorization header, from which the control plane works out who is calling.
These helpers resolve that envelope once so each endpoint is left with only its
own logic.

The token belongs to exactly one App (`AppBearerToken`), so the App — and
through it the Environment, the Organization and the owner — is *derived*.
Nothing in the request body, query string or headers can change which app is
acted on; views ignore any `app_slug` / `owner_username` a client still sends.

- `resolve_app_bearer_context(request)` — 401 on a missing or unknown token,
  otherwise the App the token belongs to.
- `resolve_app_owner(app)` — the owner User recorded by the app's
  ResourceTag(key="owner"); 404 when the app has none. Only endpoints that act
  on behalf of a person call this.

Each helper returns a `(value, JsonResponse | None)` tuple: on failure the
JsonResponse carries the right status and the value is None, so callers
short-circuit with `if error is not None: return error`.
"""

import json
import logging

from django.http import HttpRequest, JsonResponse

from humanityrules_app.models import App, ResourceTag, User
from humanityrules_app.views import app_bearer_auth

logger = logging.getLogger(__name__)


def parse_json_body(request: HttpRequest) -> tuple[dict | None, JsonResponse | None]:
    """Parse a JSON object from an API request body."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, JsonResponse({"error": "invalid JSON body"}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "JSON object body is required"}, status=400)
    return payload, None


def resolve_app_bearer_context(request: HttpRequest) -> tuple[App | None, JsonResponse | None]:
    """Resolve the calling App from its per-app bearer token."""
    raw_token = app_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return None, JsonResponse({"error": "missing bearer token"}, status=401)
    app = app_bearer_auth.resolve_app_from_token(raw_token=raw_token)
    if app is None:
        return None, JsonResponse({"error": "invalid bearer token"}, status=401)
    return app, None


def resolve_app_owner(app: App) -> tuple[User | None, JsonResponse | None]:
    """Resolve the App's owner from its ResourceTag(key="owner"); 404 when it has none.

    Ownership is recorded as a tag rather than a column (see
    template_deploy_service._stamp_template_tags), so an app can legitimately
    exist without an owner — a shared/team app. Endpoints that act on behalf of
    a person call this and fail closed; endpoints that don't need a user skip it.
    """
    owner_tag = ResourceTag.objects.filter(
        resource_type=ResourceTag.ResourceType.APP,
        app=app,
        key="owner",
    ).first()
    if owner_tag is None:
        return None, JsonResponse({"error": "app has no owner"}, status=404)
    user = User.objects.filter(
        username=owner_tag.value,
        organization_memberships__organization=app.organization,
    ).first()
    if user is None:
        return None, JsonResponse({"error": "not connected"}, status=404)
    return user, None
