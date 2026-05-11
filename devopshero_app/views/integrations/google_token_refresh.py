"""Env-resident-component → DOH refresh endpoint for Google access tokens.

See `docs/integrations_broker_design.md`. Env-resident callers
(Hermes integrations broker, etc.) hit this endpoint with `Authorization: Bearer
<DOH_ENV_BEARER>` and `{"owner_username": "..."}` in the body. DOH resolves
the environment from the bearer, looks up the user's IntegrationUserGrant
row for that env, exchanges the stored refresh token with Google using DOH's
OAuth client_secret, and returns a short-lived access token.

The refresh_token and DOH's OAuth client_secret never cross the customer/DOH
boundary. If Google has revoked the refresh_token, the row is deleted and
410 is returned so the caller surfaces a reconnect prompt to the user.
"""

import json
import logging

import httpx
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import IntegrationConfig, IntegrationUserGrant, User
from devopshero_app.views import env_bearer_auth

logger = logging.getLogger(__name__)


GOOGLE_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 30


@csrf_exempt
@require_POST
def integrations_google_token_refresh(request: HttpRequest) -> JsonResponse:
    """Exchange a stored refresh_token for a short-lived Google access token."""
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
        return JsonResponse(
            {"error": "owner_username is required"}, status=400,
        )

    try:
        google_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
    except IntegrationConfig.DoesNotExist:
        logger.error("google token refresh failed: IntegrationConfig(provider=google) missing")
        return JsonResponse({"error": "google integration not configured"}, status=500)

    user = User.objects.filter(username=owner_username).first()
    if user is None:
        logger.info(
            "google token refresh: user not found env=%s owner=%s",
            environment.slug, owner_username,
        )
        return JsonResponse({"error": "not connected"}, status=404)

    integration = IntegrationUserGrant.objects.filter(
        user=user,
        environment=environment,
        provider=IntegrationUserGrant.Provider.GOOGLE,
    ).first()
    if integration is None:
        logger.info(
            "google token refresh: no integration row env=%s owner=%s",
            environment.slug, owner_username,
        )
        return JsonResponse({"error": "not connected"}, status=404)

    exchange_result = _exchange_refresh_token(
        web=google_cfg.config,
        refresh_token=integration.refresh_token,
    )
    if exchange_result.revoked:
        logger.info(
            "google token refresh: revoked by google, deleting row env=%s owner=%s",
            environment.slug, owner_username,
        )
        integration.delete()
        return JsonResponse({"error": "revoked, please reconnect"}, status=410)
    if exchange_result.error is not None:
        logger.error(
            "google token refresh failed env=%s owner=%s error=%s",
            environment.slug, owner_username, exchange_result.error,
        )
        return JsonResponse({"error": "google token exchange failed"}, status=502)

    # Google occasionally rotates the refresh_token; persist the new one when it does.
    new_refresh = exchange_result.response.get("refresh_token")
    if new_refresh and new_refresh != integration.refresh_token:
        integration.refresh_token = new_refresh
    integration.last_refreshed_at = _now()
    integration.save(update_fields=["refresh_token", "last_refreshed_at"])

    return JsonResponse({
        "access_token": exchange_result.response["access_token"],
        "expires_in": int(exchange_result.response.get("expires_in", 0)),
        "token_type": exchange_result.response.get("token_type", "Bearer"),
    })


class _ExchangeResult:
    """Outcome of a Google refresh-token exchange.

    Exactly one of `response`, `revoked`, or `error` is populated on any
    given instance.
    """

    def __init__(self, response: dict | None, revoked: bool, error: str | None) -> None:
        self.response = response or {}
        self.revoked = revoked
        self.error = error


def _exchange_refresh_token(web: dict, refresh_token: str) -> _ExchangeResult:
    """POST to Google's token endpoint with grant_type=refresh_token.

    Classifies the response into one of three buckets:
    - revoked: Google returned 400 invalid_grant (or similar refresh-token
      rejection). The stored token is unusable; caller should delete the row.
    - error: any other failure (network, 5xx, non-JSON).
    - response: the raw JSON from Google.
    """
    try:
        response = httpx.post(
            web["token_uri"],
            data={
                "client_id": web["client_id"],
                "client_secret": web["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=GOOGLE_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return _ExchangeResult(response=None, revoked=False, error=f"network: {exc}")

    if response.status_code == 200:
        try:
            return _ExchangeResult(response=response.json(), revoked=False, error=None)
        except ValueError:
            return _ExchangeResult(response=None, revoked=False, error="non-json-200")

    # Google's refresh-token rejection returns 400 with
    # {"error": "invalid_grant", ...}. Treat as revoked.
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 400 and body.get("error") == "invalid_grant":
        return _ExchangeResult(response=None, revoked=True, error=None)

    return _ExchangeResult(
        response=None, revoked=False,
        error=f"http {response.status_code}: {body.get('error', 'unknown')}",
    )


def _now():
    """Indirection for tests to patch."""
    from django.utils import timezone
    return timezone.now()
