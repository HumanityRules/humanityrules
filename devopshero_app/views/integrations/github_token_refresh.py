"""Env-resident-component → DOH refresh endpoint for GitHub access tokens.

Mirror of `google_token_refresh.py` for GitHub user-to-server tokens.
Env-resident callers (the integrations broker / TLS-intercept proxy) hit
this endpoint with `Authorization: Bearer <DOH_ENV_BEARER>` and
`{"owner_username": "...", "app_slug": "..."}` in the body. DOH resolves the environment from
the bearer, looks up the user's IntegrationUserCredential row for that logical app,
exchanges the stored refresh_token with GitHub using DOH's `client_id` +
`client_secret`, and returns a short-lived access token.

GitHub rotates the refresh_token on every successful refresh — we always
persist the new one. A 6-month idle window invalidates the refresh; GitHub
returns 200 with `error=bad_refresh_token` (or similar). We delete the row
and 410 the caller, mirroring Google's invalid_grant path.
"""

import json
import logging

import httpx
from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import IntegrationUserCredential, User
from devopshero_app.views import env_bearer_auth


logger = logging.getLogger(__name__)


GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 30

# GitHub returns an HTTP 200 with an error JSON body when the refresh_token
# is no longer valid. These are the codes that indicate a permanent failure
# and should trigger row deletion + 410 to the caller (vs a transient error
# that should be retried).
_REVOKED_ERROR_CODES = frozenset({
    "bad_refresh_token",
    "bad_credentials",
    "unauthorized_client",
    "invalid_grant",
})


@csrf_exempt
@require_POST
def integrations_github_token_refresh(request: HttpRequest) -> JsonResponse:
    """Exchange a stored refresh_token for a short-lived GitHub access token."""
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

    owner_username = payload.get("owner_username")
    if not isinstance(owner_username, str) or not owner_username:
        return JsonResponse({"error": "owner_username is required"}, status=400)
    app_slug = payload.get("app_slug")
    if not isinstance(app_slug, str) or not app_slug:
        return JsonResponse({"error": "app_slug is required"}, status=400)

    if not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        logger.error("github token refresh failed: GITHUB_APP_CLIENT_ID/SECRET not configured")
        return JsonResponse({"error": "github integration not configured"}, status=500)

    user = User.objects.filter(
        username=owner_username,
        organization_memberships__organization=environment.aws_account.organization,
    ).first()
    if user is None:
        logger.info(
            "github token refresh: user not found env=%s owner=%s",
            environment.slug, owner_username,
        )
        return JsonResponse({"error": "not connected"}, status=404)

    integration = IntegrationUserCredential.objects.filter(
        owner_user=user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.GITHUB,
    ).first()
    if integration is None:
        logger.info(
            "github token refresh: no integration row env=%s owner=%s app=%s",
            environment.slug, owner_username, app_slug,
        )
        return JsonResponse({"error": "not connected"}, status=404)

    old_refresh = integration.credentials.get("refresh_token", "")
    if not old_refresh:
        logger.error(
            "github token refresh: row missing refresh_token env=%s owner=%s app=%s",
            environment.slug, owner_username, app_slug,
        )
        return JsonResponse({"error": "not connected"}, status=404)
    exchange_result = _exchange_refresh_token(refresh_token=old_refresh)

    # Whatever happens next, we must only mutate the row if its
    # refresh_token is still old_refresh. Two callers can read the same
    # R1, race at GitHub's token endpoint, and disagree on what the row
    # should look like — but only one of them was actually authoritative
    # (the one whose R1 the row still holds at decision time).

    if exchange_result.revoked:
        # Compare-and-swap delete: GitHub said R1 is dead, but if the row
        # has since rotated to R2 (a concurrent winner), R1 being dead is
        # expected — the row is fine. Don't delete a valid grant.
        deleted, _ = IntegrationUserCredential.objects.filter(
            id=integration.id, credentials__refresh_token=old_refresh,
        ).delete()
        if deleted:
            logger.info(
                "github token refresh: revoked by github, deleted row env=%s owner=%s app=%s",
                environment.slug, owner_username, app_slug,
            )
            return JsonResponse({"error": "revoked, please reconnect"}, status=410)
        # Row already rotated by a concurrent refresh — treat as transient.
        # Caller (broker) retries; _ensure_fresh on the next call reads the
        # winner's R2, which is valid.
        logger.info(
            "github token refresh: stale revoke (row rotated under us) env=%s owner=%s app=%s",
            environment.slug, owner_username, app_slug,
        )
        return JsonResponse({"error": "stale, retry"}, status=409)

    if exchange_result.error is not None:
        logger.error(
            "github token refresh failed env=%s owner=%s app=%s error=%s",
            environment.slug, owner_username, app_slug, exchange_result.error,
        )
        return JsonResponse({"error": "github token exchange failed"}, status=502)

    # Compare-and-swap update: only rotate if the row still holds R1. A
    # peer who also got back a successful rotation may have already
    # written R2 — in that case our new_refresh would be a now-orphan
    # value. Affected_rows == 0 just means we lost; our access_token
    # is still valid for ~8h, so we return it without persisting.
    new_refresh = exchange_result.response.get("refresh_token", "") or old_refresh
    refreshed_at = _now()
    IntegrationUserCredential.objects.filter(
        id=integration.id, credentials__refresh_token=old_refresh,
    ).update(
        credentials={**integration.credentials, "refresh_token": new_refresh},
        last_refreshed_at=refreshed_at,
        updated_at=refreshed_at,
    )

    return JsonResponse({
        "access_token": exchange_result.response["access_token"],
        "expires_in": int(exchange_result.response.get("expires_in", 0)),
        "token_type": exchange_result.response.get("token_type", "Bearer"),
    })


class _ExchangeResult:
    """Outcome of a GitHub refresh-token exchange.

    Exactly one of `response`, `revoked`, or `error` is populated.
    """

    def __init__(self, response: dict | None, revoked: bool, error: str | None) -> None:
        self.response = response or {}
        self.revoked = revoked
        self.error = error


def _exchange_refresh_token(refresh_token: str) -> _ExchangeResult:
    """POST to GitHub's token endpoint with grant_type=refresh_token.

    GitHub's quirk: failed refreshes return HTTP 200 with an error body, not
    a 4xx. Classification keys off the JSON `error` field, not status code.
    """
    try:
        response = httpx.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_APP_CLIENT_ID,
                "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=GITHUB_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return _ExchangeResult(response=None, revoked=False, error=f"network: {exc}")

    if response.status_code != 200:
        return _ExchangeResult(
            response=None, revoked=False,
            error=f"http {response.status_code}",
        )

    try:
        body = response.json()
    except ValueError:
        return _ExchangeResult(response=None, revoked=False, error="non-json-200")

    if "error" in body:
        if body["error"] in _REVOKED_ERROR_CODES:
            return _ExchangeResult(response=None, revoked=True, error=None)
        return _ExchangeResult(
            response=None, revoked=False,
            error=f"github error: {body['error']}",
        )

    if "access_token" not in body:
        return _ExchangeResult(
            response=None, revoked=False,
            error="missing access_token in response",
        )

    return _ExchangeResult(response=body, revoked=False, error=None)


def _now():
    """Indirection for tests to patch."""
    from django.utils import timezone
    return timezone.now()
