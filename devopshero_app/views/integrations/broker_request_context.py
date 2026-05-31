"""Shared request plumbing for broker-to-DOH integration endpoints.

The env-resident Hermes broker calls several DOH endpoints (batched token
refresh, credential setup/submit, disconnect) with the same shape: an env
bearer in the Authorization header and a JSON body naming an owner_username
and app_slug. These helpers resolve and validate that shared envelope so each
endpoint is left with only its own logic.

Each helper returns a `(value, JsonResponse | None)` tuple: on failure the
JsonResponse carries the right status and the value is None, so callers
short-circuit with `if error is not None: return error`.
"""

import json
import logging

from django.http import HttpRequest, JsonResponse

from devopshero_app.models import App, Environment, ResourceTag, User
from devopshero_app.views import env_bearer_auth

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


def resolve_env_bearer_context(request: HttpRequest) -> tuple[Environment | None, JsonResponse | None]:
    """Resolve the env bearer used by broker-to-DOH integration endpoints."""
    raw_token = env_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return None, JsonResponse({"error": "missing bearer token"}, status=401)
    environment = env_bearer_auth.resolve_env_from_token(raw_token=raw_token)
    if environment is None:
        return None, JsonResponse({"error": "invalid bearer token"}, status=401)
    return environment, None


def resolve_owner_user(owner_username: object, environment: Environment) -> tuple[User | None, JsonResponse | None]:
    """Resolve and validate the owner user for the environment's organization."""
    if not isinstance(owner_username, str) or not owner_username:
        return None, JsonResponse({"error": "owner_username is required"}, status=400)
    user = User.objects.filter(
        username=owner_username,
        organization_memberships__organization=environment.aws_account.organization,
    ).first()
    if user is None:
        return None, JsonResponse({"error": "not connected"}, status=404)
    return user, None


def resolve_owned_app_slug(app_slug: object, environment: Environment, owner_user: User) -> tuple[str | None, JsonResponse | None]:
    """Resolve app_slug and verify it belongs to owner_user in the env's organization."""
    if not isinstance(app_slug, str) or not app_slug:
        return None, JsonResponse({"error": "app_slug is required"}, status=400)
    app = App.objects.filter(
        organization=environment.aws_account.organization,
        slug=app_slug,
    ).first()
    if app is None:
        return None, JsonResponse({"error": "app not found"}, status=404)
    owner_tag = ResourceTag.objects.filter(
        resource_type=ResourceTag.ResourceType.APP,
        app=app,
        key="owner",
        value=owner_user.username,
    ).first()
    if owner_tag is None:
        return None, JsonResponse({"error": "app is not owned by requested user"}, status=403)
    return app.slug, None
