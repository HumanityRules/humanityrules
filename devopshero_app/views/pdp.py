"""
Policy Decision Point (PDP) endpoint — called by policy proxies inside customer
environments to authorize each request. See docs/policy_proxy_design.md.

Auth: Bearer token from the env's shared-secrets (DOH_POLICY_PROXY_TOKEN). The
token identifies the Environment; the environment's organization then scopes
the ABAC lookup.
"""

import hashlib
import hmac
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..models import App, DeploymentBlueprint, PolicyProxyToken, User
from ..services import abac

logger = logging.getLogger(__name__)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _extract_bearer_token(request: HttpRequest) -> str | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header[len("Bearer "):].strip() or None


def _resolve_env_from_token(raw_token: str):
    """Return the Environment matching *raw_token*, or None."""
    token_hash = _hash_token(raw_token)
    # Constant-time comparison across all rows: fetch hash-matching row by index,
    # then compare digests with hmac.compare_digest to guard against any timing
    # signal in the equality test.
    row = PolicyProxyToken.objects.select_related(
        "environment", "environment__aws_account__organization",
    ).filter(token_hash=token_hash).first()
    if row is None:
        return None
    if not hmac.compare_digest(row.token_hash, token_hash):
        return None
    return row.environment


@csrf_exempt
@require_POST
def pdp_evaluate(request: HttpRequest) -> JsonResponse:
    """Evaluate a single (identity, app, path) decision. Returns {decision, reason}."""
    raw_token = _extract_bearer_token(request)
    if raw_token is None:
        return JsonResponse({"error": "missing bearer token"}, status=401)

    environment = _resolve_env_from_token(raw_token)
    if environment is None:
        return JsonResponse({"error": "invalid bearer token"}, status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    app_id = payload.get("app_id")
    oidc_sub = payload.get("oidc_sub")
    username = payload.get("username")
    path = payload.get("path", "")
    if not (isinstance(app_id, str) and isinstance(oidc_sub, str) and isinstance(username, str)):
        return JsonResponse(
            {"error": "app_id, oidc_sub, and username are required strings"},
            status=400,
        )

    organization = environment.aws_account.organization

    app = App.objects.filter(organization=organization, slug=app_id).first()
    if app is None:
        logger.info(
            "pdp deny reason=app-not-in-org env=%s app_id=%s oidc_sub=%s path=%s",
            environment.slug, app_id, oidc_sub, path,
        )
        return JsonResponse({"decision": "deny", "reason": "app-not-in-org"})

    blueprint_exists = DeploymentBlueprint.objects.filter(app=app, environment=environment).exists()
    if not blueprint_exists:
        logger.info(
            "pdp deny reason=app-not-in-env env=%s app=%s oidc_sub=%s path=%s",
            environment.slug, app.slug, oidc_sub, path,
        )
        return JsonResponse({"decision": "deny", "reason": "app-not-in-env"})

    user = User.objects.filter(oidc_sub=oidc_sub).first()
    if user is None:
        logger.info(
            "pdp deny reason=user-not-found env=%s app=%s oidc_sub=%s path=%s",
            environment.slug, app.slug, oidc_sub, path,
        )
        return JsonResponse({"decision": "deny", "reason": "user-not-found"})

    allowed = abac.evaluate_policies(
        organization=organization, user=user, resource=app, resource_type="app",
    )
    if "app:use" in allowed:
        logger.info(
            "pdp allow env=%s app=%s username=%s oidc_sub=%s path=%s",
            environment.slug, app.slug, username, oidc_sub, path,
        )
        return JsonResponse({"decision": "allow", "reason": "app:use"})

    logger.info(
        "pdp deny reason=no-matching-policy env=%s app=%s username=%s oidc_sub=%s path=%s",
        environment.slug, app.slug, username, oidc_sub, path,
    )
    return JsonResponse({"decision": "deny", "reason": "no-matching-policy"})
