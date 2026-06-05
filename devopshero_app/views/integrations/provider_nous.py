"""Nous Portal per-user integration: device-flow connect + token refresh.

Nous connects via the upstream Hermes Portal device flow. The env-resident
broker drives that flow, sends the resulting refresh token to DOH, and DOH
exchanges that stored refresh token for short-lived inference access tokens.
The sandbox sees only the DOH placeholder; the TLS-intercept proxy injects the
fresh bearer on requests to inference-api.nousresearch.com.
"""

import logging

import httpx
from django.utils import timezone

from devopshero_app.models import Environment, IntegrationUserCredential, User
from devopshero_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


NOUS_PORTAL_BASE_URL = "https://portal.nousresearch.com"
NOUS_INFERENCE_BASE_URL = "https://inference-api.nousresearch.com/v1"
NOUS_OAUTH_CLIENT_ID = "hermes-cli"
NOUS_OAUTH_TOKEN_URL = f"{NOUS_PORTAL_BASE_URL}/api/oauth/token"
NOUS_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5
NOUS_DEFAULT_EXPIRES_IN = 3600
NOUS_REVOKING_ERROR_CODES = frozenset({"invalid_grant", "invalid_token", "refresh_token_reused"})


def _coerce_expires_in(value: object, fallback: int) -> int:
    """Return a positive expires_in integer from an OAuth response value."""
    try:
        expires_in = int(value)
    except (TypeError, ValueError):
        return fallback
    return expires_in if expires_in > 0 else fallback


def _exchange_refresh_token(refresh_token: str) -> provider_common.ExchangeResult:
    """POST grant_type=refresh_token to Nous Portal and classify the result."""
    try:
        response = httpx.post(
            NOUS_OAUTH_TOKEN_URL,
            headers={"x-nous-refresh-token": refresh_token},
            data={"grant_type": "refresh_token", "client_id": NOUS_OAUTH_CLIENT_ID},
            timeout=NOUS_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return provider_common.ExchangeResult(response=None, revoked=False, error=f"network: {exc}")

    if response.status_code == 200:
        try:
            return provider_common.ExchangeResult(response=response.json(), revoked=False, error=None)
        except ValueError:
            return provider_common.ExchangeResult(response=None, revoked=False, error="non-json-200")

    try:
        body = response.json()
    except ValueError:
        body = {}
    err_code = str(body.get("error") or "")
    if response.status_code in (400, 401) and err_code in NOUS_REVOKING_ERROR_CODES:
        return provider_common.ExchangeResult(response=None, revoked=True, error=None)
    return provider_common.ExchangeResult(
        response=None,
        revoked=False,
        error=f"http {response.status_code}: {err_code or 'unknown'}",
    )


def store_device_credentials(environment: Environment, owner_user: User, app_slug: str, payload: dict) -> tuple[int, dict]:
    """Store the refresh token the broker obtained from Nous Portal's device flow."""
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        return 400, {"error": "refresh_token is required"}

    metadata = {
        "connected_at": timezone.now().isoformat(),
        "portal_base_url": str(payload.get("portal_base_url") or NOUS_PORTAL_BASE_URL),
        "inference_base_url": str(payload.get("inference_base_url") or NOUS_INFERENCE_BASE_URL),
    }
    for key in ("scope", "token_type"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value

    IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.NOUS,
        defaults={
            "credentials": {"refresh_token": refresh_token},
            "config": {},
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    logger.info("nous integration stored env=%s owner=%s app=%s", environment.slug, owner_user.username, app_slug)
    return 200, {"ok": True, "provider": IntegrationUserCredential.Provider.NOUS, "status": "connected"}


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one Nous Portal credential."""
    integration = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.NOUS,
    ).first()
    if integration is None:
        return provider_common.absent()

    refresh_token = integration.credentials.get("refresh_token", "")
    if not refresh_token:
        logger.error("nous token refresh: row missing refresh_token env=%s owner=%s app=%s", environment.slug, owner_user.username, app_slug)
        return provider_common.absent()

    exchange_result = _exchange_refresh_token(refresh_token=refresh_token)
    if exchange_result.revoked:
        logger.info("nous token refresh: revoked by Portal, deleting row env=%s owner=%s app=%s", environment.slug, owner_user.username, app_slug)
        integration.delete()
        return provider_common.absent()
    if exchange_result.error is not None:
        logger.error("nous token refresh failed env=%s owner=%s app=%s error=%s", environment.slug, owner_user.username, app_slug, exchange_result.error)
        return provider_common.transient()

    access_token = exchange_result.response.get("access_token", "")
    if not access_token:
        logger.error("nous token refresh: response missing access_token env=%s owner=%s app=%s", environment.slug, owner_user.username, app_slug)
        return provider_common.transient()

    new_refresh = exchange_result.response.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        integration.credentials = {**integration.credentials, "refresh_token": new_refresh}
    metadata = dict(integration.metadata)
    for key in ("scope", "token_type", "inference_base_url"):
        value = exchange_result.response.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    integration.metadata = metadata
    integration.last_refreshed_at = provider_common.now()
    integration.save(update_fields=["credentials", "metadata", "last_refreshed_at", "updated_at"])

    fallback_expires_in = _coerce_expires_in(value=exchange_result.response.get("expires_in"), fallback=NOUS_DEFAULT_EXPIRES_IN)
    return provider_common.has_token(
        secrets={"access_token": access_token},
        expires_in=provider_common.expires_in_from_access_token(
            access_token=access_token,
            fallback=fallback_expires_in,
        ),
        config={},
        metadata={},
    )


def revoke(refresh_token: str) -> None:
    """No-op: the public Hermes Nous OAuth client exposes no revocation endpoint here."""
    _ = refresh_token
