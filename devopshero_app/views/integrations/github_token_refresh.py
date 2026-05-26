"""GitHub access-token refresh helper invoked by the batched DOH refresh endpoint.

Mirror of `google_token_refresh.py` for GitHub user-to-server tokens.
`refresh_github_outcome` exchanges the user's stored refresh_token with
GitHub using DOH's `client_id` + `client_secret` and returns a
broker-shaped `{outcome, access_token?, expires_in?, config, metadata}`
dict. The batched endpoint (`views/integrations/token_refresh_batch.py`)
calls this from a worker thread alongside the other providers.

GitHub rotates the refresh_token on every successful refresh — we always
persist the new one. A 6-month idle window invalidates the refresh; GitHub
returns 200 with `error=bad_refresh_token` (or similar). We delete the
row and the outcome flips to `absent` so the WebUI extension prompts the
user to reconnect, mirroring Google's invalid_grant path.
"""

import logging

import httpx
from django.conf import settings

from devopshero_app.models import Environment, IntegrationUserCredential, User


logger = logging.getLogger(__name__)


GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
# GitHub's refresh-token exchange is a single small POST. Healthy P99 is
# well under 1s; setting the ceiling at 5s means the broker's batch
# refresh (which runs all providers in parallel) is naturally bounded by
# the slowest single exchange, no separate batch deadline needed. If
# upstream is taking longer than 5s, surfacing `transient` (cache-
# preserving) beats hanging a user request on a refresh that's about to
# fail anyway.
GITHUB_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5

# GitHub returns an HTTP 200 with an error JSON body when the refresh_token
# is no longer valid. These are the codes that indicate a permanent failure
# and should trigger row deletion (the outcome flips to `absent` so the
# UI prompts a reconnect), vs a transient error that should be retried.
_REVOKED_ERROR_CODES = frozenset({
    "bad_refresh_token",
    "bad_credentials",
    "unauthorized_client",
    "invalid_grant",
})


def refresh_github_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one (env, owner, app).

    Mirror of `refresh_google_outcome` for GitHub. CAS-delete-on-revoked
    and CAS-update-on-rotate semantics are preserved (see inline comments).
    A lost CAS race surfaces as `transient`, just like a network blip:
    the next refresh sees the winner's rotated R2.
    """
    if not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        logger.error("github token refresh failed: GITHUB_APP_CLIENT_ID/SECRET not configured")
        return {"outcome": "transient"}

    integration = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.GITHUB,
    ).first()
    if integration is None:
        return {"outcome": "absent"}

    old_refresh = integration.credentials.get("refresh_token", "")
    if not old_refresh:
        logger.error(
            "github token refresh: row missing refresh_token env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        return {"outcome": "absent"}
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
                environment.slug, owner_user.username, app_slug,
            )
            return {"outcome": "absent"}
        # Row already rotated by a concurrent refresh — surface as
        # transient so we don't overwrite the winner's cache; the next
        # call reads the winner's R2.
        logger.info(
            "github token refresh: stale revoke (row rotated under us) env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        return {"outcome": "transient"}

    if exchange_result.error is not None:
        logger.error(
            "github token refresh failed env=%s owner=%s app=%s error=%s",
            environment.slug, owner_user.username, app_slug, exchange_result.error,
        )
        return {"outcome": "transient"}

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

    return {
        "outcome": "has_token",
        "access_token": exchange_result.response["access_token"],
        "expires_in": int(exchange_result.response.get("expires_in", 0)),
        "config": {},
        "metadata": {},
    }


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
