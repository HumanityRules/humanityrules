"""
Policy Decision Point (PDP) endpoint — called by policy proxies inside customer
environments to authorize each request. See docs/policy_proxy_design.md.

Auth: the per-app bearer token from the app's own secrets bag (HUMR_APP_BEARER).
Presenting it proves which App is asking, so the App — and through it the
Environment and the Organization that scopes the ABAC lookup — is derived here,
never read from the request. What the request does carry is the *end user* whose
access is being decided (`sub`, `provider`, `username`) and the `path`.
"""

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..models import User, WebappPublicGrant
from ..services import abac_service
from . import app_bearer_auth

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def pdp_evaluate_public(request: HttpRequest) -> JsonResponse:
    """Anonymous decision for one webapp hostname: allow iff a live WebappPublicGrant exists.

    Called by the policy proxy when a request's Host parses as
    <webapp_slug>-<agent-host>, before (and instead of) any session identity
    check. A deny here sends the proxy down its normal cookie/ABAC path.
    """
    raw_token = app_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return JsonResponse({"error": "missing bearer token"}, status=401)

    app = app_bearer_auth.resolve_app_from_token(raw_token=raw_token)
    if app is None:
        return JsonResponse({"error": "invalid bearer token"}, status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    webapp_slug = payload.get("webapp_slug")
    if not isinstance(webapp_slug, str):
        return JsonResponse({"error": "webapp_slug is required and must be a string"}, status=400)

    environment = app.environment

    grant_is_live = WebappPublicGrant.live().filter(app=app, slug=webapp_slug).exists()
    if grant_is_live:
        logger.info("pdp-public allow env=%s app=%s webapp=%s", environment.slug, app.slug, webapp_slug)
        return JsonResponse({"decision": "allow", "reason": "public-webapp"})

    logger.info("pdp-public deny reason=not-public env=%s app=%s webapp=%s", environment.slug, app.slug, webapp_slug)
    return JsonResponse({"decision": "deny", "reason": "not-public"})


@csrf_exempt
@require_POST
def pdp_evaluate(request: HttpRequest) -> JsonResponse:
    """Evaluate a single (identity, app, path) decision. Returns {decision, reason}."""
    raw_token = app_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return JsonResponse({"error": "missing bearer token"}, status=401)

    app = app_bearer_auth.resolve_app_from_token(raw_token=raw_token)
    if app is None:
        return JsonResponse({"error": "invalid bearer token"}, status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    sub = payload.get("sub")
    provider = payload.get("provider")
    username = payload.get("username")
    path = payload.get("path", "")
    if not (isinstance(sub, str) and isinstance(username, str) and isinstance(provider, str)):
        return JsonResponse(
            {"error": "sub, provider, and username are required strings"},
            status=400,
        )
    if provider not in ("workos", "oidc"):
        return JsonResponse({"error": f"unknown provider {provider!r}"}, status=400)

    environment = app.environment

    # Platform admins bypass ABAC on every HA. Deliberately unscoped user
    # lookup: the admin is cross-tenant and typically not a member of the
    # app's organization.
    sub_column = "workos_user_id" if provider == "workos" else "oidc_sub"
    if User.objects.filter(**{sub_column: sub}, is_superuser=True).exists():
        logger.info(
            "pdp allow reason=platform-admin env=%s app=%s username=%s provider=%s sub=%s path=%s",
            environment.slug, app.slug, username, provider, sub, path,
        )
        return JsonResponse({"decision": "allow", "reason": "platform-admin"})

    organization = app.organization

    # WorkOS users carry sub=workos_user_id; OIDC users carry sub=<oidc subject>.
    # The session JWT's `provider` claim picks which column we look up against.
    if provider == "workos":
        user = User.objects.filter(
            workos_user_id=sub,
            organization_memberships__organization=organization,
        ).first()
    else:
        user = User.objects.filter(
            oidc_sub=sub,
            organization_memberships__organization=organization,
        ).first()
    if user is None:
        logger.info(
            "pdp deny reason=user-not-found env=%s app=%s provider=%s sub=%s path=%s",
            environment.slug, app.slug, provider, sub, path,
        )
        return JsonResponse({"decision": "deny", "reason": "user-not-found"})

    allowed = abac_service.evaluate_policies(
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
