"""Nous Portal per-user integration: device-flow connect + token refresh.

Nous connects via the upstream Hermes Portal device flow. The env-resident
broker drives that flow, sends the resulting refresh token to HUMR, and HUMR
exchanges that stored refresh token for short-lived inference access tokens.
The sandbox sees only the HUMR placeholder; the TLS-intercept proxy injects the
fresh bearer on requests to inference-api.nousresearch.com.
"""

import logging

import httpx

from humanityrules_app.models import (
    Environment,
    IntegrationSharedCredential,
    IntegrationUserCredential,
    PlatformSharedCredential,
    User,
)
from humanityrules_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


NOUS_PORTAL_BASE_URL = "https://portal.nousresearch.com"
NOUS_OAUTH_CLIENT_ID = "hermes-cli"
NOUS_OAUTH_TOKEN_URL = f"{NOUS_PORTAL_BASE_URL}/api/oauth/token"
NOUS_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5
NOUS_DEFAULT_EXPIRES_IN = 3600
NOUS_REVOKING_ERROR_CODES = frozenset({"invalid_grant", "invalid_token", "refresh_token_reused"})

# Device-flow endpoints for the control-plane-run connect (shared Nous credentials,
# where there is no per-user broker to drive the handshake). Standard RFC 8628 device
# code grant. Mirrors the broker adapter in humr_runtime/integrations/device_flow.py.
NOUS_DEVICE_CODE_URL = f"{NOUS_PORTAL_BASE_URL}/api/oauth/device/code"
NOUS_DEVICE_DEFAULT_SCOPE = "inference:invoke inference:mint_agent_key"
NOUS_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
NOUS_DEVICE_MIN_POLL_SECONDS = 1


def _exchange_refresh_token(refresh_token: str) -> provider_common.ExchangeResult:
    """POST grant_type=refresh_token to Nous Portal and classify the result."""
    return provider_common.exchange_refresh_token(
        send=lambda: httpx.post(
            NOUS_OAUTH_TOKEN_URL,
            headers={"x-nous-refresh-token": refresh_token},
            data={"grant_type": "refresh_token", "client_id": NOUS_OAUTH_CLIENT_ID},
            timeout=NOUS_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
        ),
        revoking_statuses=frozenset({400, 401}),
        revoking_error_codes=NOUS_REVOKING_ERROR_CODES,
        error_code_of=lambda body: str(body.get("error") or ""),
    )


def device_authorize() -> tuple[provider_common.DeviceAuthorization | None, str | None]:
    """Begin Nous Portal's device flow on the control plane: get a user code + verification URL.

    Used by the shared-credential connect UI (no per-user broker drives the flow).
    Returns (authorization, None) or (None, human-facing error).
    """
    try:
        response = httpx.post(
            NOUS_DEVICE_CODE_URL,
            headers={"Accept": "application/json"},
            data={"client_id": NOUS_OAUTH_CLIENT_ID, "scope": NOUS_DEVICE_DEFAULT_SCOPE},
            timeout=provider_common.DEVICE_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return None, f"Could not reach Nous Portal to start the login ({exc})."
    if response.status_code != 200:
        return None, f"Nous Portal rejected the login start (HTTP {response.status_code})."
    try:
        body = response.json()
    except ValueError:
        return None, "Nous Portal returned an unreadable login-start response."
    device_code = body.get("device_code")
    user_code = body.get("user_code")
    verification_uri = body.get("verification_uri_complete")
    expires_in = body.get("expires_in")
    interval = body.get("interval")
    if not device_code or not user_code or not verification_uri or not expires_in or not interval:
        return None, "Nous Portal's login-start response was incomplete."
    return provider_common.DeviceAuthorization(
        user_code=str(user_code),
        verification_uri=str(verification_uri),
        interval=max(NOUS_DEVICE_MIN_POLL_SECONDS, int(interval)),
        expires_in=int(expires_in),
        opaque={"device_code": str(device_code)},
    ), None


def device_poll(opaque: dict) -> provider_common.DevicePollResult:
    """Run ONE Nous Portal device-poll attempt; on approval return the token pair."""
    try:
        response = httpx.post(
            NOUS_OAUTH_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "grant_type": NOUS_DEVICE_GRANT_TYPE,
                "client_id": NOUS_OAUTH_CLIENT_ID,
                "device_code": opaque.get("device_code", ""),
            },
            timeout=provider_common.DEVICE_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return provider_common.device_failed(error=f"Could not reach Nous Portal while polling ({exc}).")
    if response.status_code == 200:
        try:
            body = response.json()
        except ValueError:
            return provider_common.device_failed(error="Nous Portal returned an unreadable token response.")
        refresh_token = body.get("refresh_token")
        if not refresh_token:
            return provider_common.device_failed(error="Nous Portal did not return a refresh token.")
        return provider_common.device_completed(refresh_token=str(refresh_token), row_metadata={})
    error_code = _device_error_code(response=response)
    # `slow_down` asks for a longer interval; the browser's fixed self-poll cadence
    # already paces us, so treat it like `authorization_pending` and keep waiting.
    if error_code in ("authorization_pending", "slow_down"):
        return provider_common.device_pending()
    return provider_common.device_failed(error=f"Nous Portal poll failed ({error_code or 'unknown error'}).")


def _device_error_code(response: httpx.Response) -> str:
    """Pull an OAuth error code from a non-200 Nous device-poll response."""
    try:
        body = response.json()
    except ValueError:
        return f"http {response.status_code}"
    return str(body.get("error") or f"http {response.status_code}")


def store_device_credentials(environment: Environment, owner_user: User, app_slug: str, payload: dict) -> tuple[int, dict]:
    """Store the refresh token the broker obtained from Nous Portal's device flow."""
    return provider_common.store_oauth_refresh_credential(
        provider=IntegrationUserCredential.Provider.NOUS,
        environment=environment,
        owner_user=owner_user,
        app_slug=app_slug,
        refresh_token=payload.get("refresh_token"),
        metadata={},
    )


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one Nous Portal credential."""
    def build_secrets(access_token: str, response: dict) -> provider_common.RefreshSecrets:
        return provider_common.RefreshSecrets(
            secrets={"access_token": access_token},
            expires_in=provider_common.expires_in_from_access_token(access_token=access_token, fallback=NOUS_DEFAULT_EXPIRES_IN),
            row_metadata={},
            row_config={},
        )

    return provider_common.run_refresh_exchange(
        provider=IntegrationUserCredential.Provider.NOUS,
        logger=logger,
        environment=environment,
        owner_user=owner_user,
        app_slug=app_slug,
        exchange=_exchange_refresh_token,
        build_secrets=build_secrets,
        outcome_metadata=None,
        tombstone_on_revoke=False,
    )


def refresh_outcome_from_shared(credential: IntegrationSharedCredential | PlatformSharedCredential) -> dict:
    """Central refresh + cache for a shared (platform or org) Nous credential.

    Exchanges the shared refresh_token once on the control plane and caches the
    access token on the row, fanning it out to every requesting broker. See
    `docs/platform_shared_credentials_design.md` (Phase 2).
    """
    def build_secrets(access_token: str, response: dict) -> provider_common.RefreshSecrets:
        return provider_common.RefreshSecrets(
            secrets={"access_token": access_token},
            expires_in=provider_common.expires_in_from_access_token(access_token=access_token, fallback=NOUS_DEFAULT_EXPIRES_IN),
            row_metadata={},
            row_config={},
        )

    return provider_common.run_shared_refresh_exchange(
        credential=credential,
        logger=logger,
        margin_seconds=provider_common.SHARED_TOKEN_REFRESH_MARGIN_SECONDS,
        exchange=_exchange_refresh_token,
        build_secrets=build_secrets,
    )


def revoke(refresh_token: str) -> None:
    """No-op: the public Hermes Nous OAuth client exposes no revocation endpoint here."""
    _ = refresh_token
