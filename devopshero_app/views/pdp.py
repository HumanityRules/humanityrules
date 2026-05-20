"""
Policy Decision Point (PDP) endpoint — called by policy proxies inside customer
environments to authorize each request. See docs/policy_proxy_design.md.

Auth: env bearer token from the env's shared-secrets (DOH_ENV_BEARER). The
token identifies the Environment; the environment's organization then scopes
the ABAC lookup.
"""

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..models import App, DeploymentBlueprint, User
from ..services import abac
from . import env_bearer_auth

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def pdp_evaluate(request: HttpRequest) -> JsonResponse:
    """Evaluate a single (identity, app, path) decision. Returns {decision, reason}."""
    raw_token = env_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return JsonResponse({"error": "missing bearer token"}, status=401)

    environment = env_bearer_auth.resolve_env_from_token(raw_token=raw_token)
    if environment is None:
        return JsonResponse({"error": "invalid bearer token"}, status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    app_id = payload.get("app_id")
    sub = payload.get("sub")
    username = payload.get("username")
    provider = payload.get("provider")
    path = payload.get("path", "")
    if not (isinstance(app_id, str) and isinstance(sub, str) and isinstance(username, str) and isinstance(provider, str)):
        return JsonResponse(
            {"error": "app_id, sub, username, and provider are required strings"},
            status=400,
        )
    if provider not in ("oidc", "workos"):
        return JsonResponse({"error": f"unsupported provider {provider!r}"}, status=400)

    organization = environment.aws_account.organization

    app = App.objects.filter(organization=organization, slug=app_id).first()
    if app is None:
        logger.info(
            "pdp deny reason=app-not-in-org env=%s app_id=%s provider=%s sub=%s path=%s",
            environment.slug, app_id, provider, sub, path,
        )
        return JsonResponse({"decision": "deny", "reason": "app-not-in-org"})

    blueprint_exists = DeploymentBlueprint.objects.filter(app=app, environment=environment).exists()
    if not blueprint_exists:
        logger.info(
            "pdp deny reason=app-not-in-env env=%s app=%s provider=%s sub=%s path=%s",
            environment.slug, app.slug, provider, sub, path,
        )
        return JsonResponse({"decision": "deny", "reason": "app-not-in-env"})

    user = _find_user_by_provider_sub(provider=provider, sub=sub)
    if user is None:
        logger.info(
            "pdp deny reason=user-not-found env=%s app=%s provider=%s sub=%s path=%s",
            environment.slug, app.slug, provider, sub, path,
        )
        return JsonResponse({"decision": "deny", "reason": "user-not-found"})

    allowed = abac.evaluate_policies(
        organization=organization, user=user, resource=app, resource_type="app",
    )
    if "app:use" in allowed:
        logger.info(
            "pdp allow env=%s app=%s username=%s provider=%s sub=%s path=%s",
            environment.slug, app.slug, username, provider, sub, path,
        )
        return JsonResponse({"decision": "allow", "reason": "app:use"})

    logger.info(
        "pdp deny reason=no-matching-policy env=%s app=%s username=%s provider=%s sub=%s path=%s",
        environment.slug, app.slug, username, provider, sub, path,
    )
    return JsonResponse({"decision": "deny", "reason": "no-matching-policy"})


def _find_user_by_provider_sub(provider: str, sub: str) -> User | None:
    """Look the User row up against the column matching the IdP that minted `sub`."""
    if provider == "workos":
        return User.objects.filter(workos_user_id=sub).first()
    return User.objects.filter(oidc_sub=sub).first()
