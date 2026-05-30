"""Google access-token refresh helper invoked by the batched DOH refresh endpoint.

`refresh_google_outcome` exchanges the user's stored refresh_token with
Google using DOH's OAuth client_secret and returns a broker-shaped
`{outcome, secrets?, expires_in?, config, metadata}` dict. The
batched endpoint (`views/integrations/token_refresh_batch.py`) calls
this from a worker thread alongside the other providers.

The refresh_token and DOH's OAuth client_secret never cross the
customer/DOH boundary. If Google has revoked the refresh_token, the row
is deleted and the outcome flips to `absent` so the WebUI extension
prompts the user to reconnect.
"""

import logging

import httpx

from devopshero_app.models import Environment, IntegrationConfig, IntegrationUserCredential, User

logger = logging.getLogger(__name__)


# Google's refresh-token exchange is a single small POST. Healthy P99 is
# well under 1s; setting the ceiling at 5s means the broker's batch
# refresh (which runs all providers in parallel) is naturally bounded by
# the slowest single exchange, no separate batch deadline needed. If
# upstream is taking longer than 5s, surfacing `transient` (cache-
# preserving) beats hanging a user request on a refresh that's about to
# fail anyway.
GOOGLE_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5


def refresh_google_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one (env, owner, app).

    Returns `{outcome, secrets?, expires_in?, config?, metadata?}`
    where `outcome` is `"has_token" | "absent" | "transient"`. The
    `absent` (disconnected) path is intentionally silent — the broker
    asks every Refresh-all/bootstrap, and most providers are typically
    disconnected, so logging that as an error would be log spam.
    """
    try:
        google_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
    except IntegrationConfig.DoesNotExist:
        logger.error("google token refresh failed: IntegrationConfig(provider=google) missing")
        return {"outcome": "transient"}

    integration = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.GOOGLE,
    ).first()
    if integration is None:
        return {"outcome": "absent"}

    refresh_token = integration.credentials.get("refresh_token", "")
    if not refresh_token:
        logger.error(
            "google token refresh: row missing refresh_token env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        return {"outcome": "absent"}

    exchange_result = _exchange_refresh_token(
        web=google_cfg.config,
        refresh_token=refresh_token,
    )
    if exchange_result.revoked:
        logger.info(
            "google token refresh: revoked by google, deleting row env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        integration.delete()
        return {"outcome": "absent"}
    if exchange_result.error is not None:
        logger.error(
            "google token refresh failed env=%s owner=%s app=%s error=%s",
            environment.slug, owner_user.username, app_slug, exchange_result.error,
        )
        return {"outcome": "transient"}

    new_refresh = exchange_result.response.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        integration.credentials = {**integration.credentials, "refresh_token": new_refresh}
    integration.last_refreshed_at = _now()
    integration.save(update_fields=["credentials", "last_refreshed_at", "updated_at"])

    return {
        "outcome": "has_token",
        "secrets": {"access_token": exchange_result.response["access_token"]},
        "expires_in": int(exchange_result.response.get("expires_in", 0)),
        "config": {},
        "metadata": {},
    }


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
