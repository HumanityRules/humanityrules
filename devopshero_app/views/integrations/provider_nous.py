"""Nous Portal per-user integration: device-flow connect + token refresh.

Nous connects via the upstream Hermes Portal device flow. The env-resident
broker drives that flow, sends the resulting refresh token to DOH, and DOH
exchanges that stored refresh token for short-lived inference access tokens.
The sandbox sees only the DOH placeholder; the TLS-intercept proxy injects the
fresh bearer on requests to inference-api.nousresearch.com.
"""

import logging

import httpx

from devopshero_app.models import Environment, IntegrationUserCredential, User
from devopshero_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


NOUS_PORTAL_BASE_URL = "https://portal.nousresearch.com"
NOUS_OAUTH_CLIENT_ID = "hermes-cli"
NOUS_OAUTH_TOKEN_URL = f"{NOUS_PORTAL_BASE_URL}/api/oauth/token"
NOUS_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5
NOUS_DEFAULT_EXPIRES_IN = 3600
NOUS_REVOKING_ERROR_CODES = frozenset({"invalid_grant", "invalid_token", "refresh_token_reused"})


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
        )

    return provider_common.run_refresh_exchange(
        provider=IntegrationUserCredential.Provider.NOUS,
        logger=logger,
        environment=environment,
        owner_user=owner_user,
        app_slug=app_slug,
        exchange=_exchange_refresh_token,
        build_secrets=build_secrets,
    )


def revoke(refresh_token: str) -> None:
    """No-op: the public Hermes Nous OAuth client exposes no revocation endpoint here."""
    _ = refresh_token
