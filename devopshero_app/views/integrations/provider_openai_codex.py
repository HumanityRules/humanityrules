"""OpenAI Codex (ChatGPT-subscription) per-user integration: device-flow connect + token refresh.

Unlike the redirect-dance OAuth providers (Google, GitHub), Codex connects via
OpenAI's device flow run *by the env-resident broker* (see
`doh_runtime/integrations_broker.py`). The broker drives the device handshake,
then POSTs the resulting refresh_token to DOH at
`/api/integrations/credentials/codex-device-complete`, which stores it as an
IntegrationUserCredential row. No browser redirect, no callback of ours, no
DOH-held client secret — Codex's client is a public OAuth client.

Refresh (`refresh_outcome`): exchanges the stored refresh_token at
`auth.openai.com/oauth/token` (grant_type=refresh_token, public client_id, no
secret) for a fresh access token, derives the `chatgpt_account_id` claim from
the access-token JWT, and returns BOTH as broker secrets. The broker injects
`Authorization: Bearer <access_token>` and `ChatGPT-Account-ID: <account_id>`
on the wire to chatgpt.com (credential model A1: the account id never enters
the sandbox). If OpenAI has revoked the refresh_token the row is deleted and
the outcome flips to `absent`.

See `docs/openai_codex_integration_design.md`.
"""

import base64
import binascii
import json
import logging
import time

import httpx

from devopshero_app.models import Environment, IntegrationUserCredential, User
from devopshero_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


# Codex's first-party public OAuth client. Constant, secretless — there is no
# IntegrationConfig row for this provider (contrast Google/GitHub, which carry
# a client_secret). Sourced from the Codex CLI / Hermes codex login path.
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"

# One small POST, same budget rationale as Google's exchange: bound the batched
# refresh by the slowest single provider, surface `transient` rather than hang.
CODEX_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5

# Fallback access-token lifetime when neither the JWT `exp` nor the response's
# `expires_in` is usable. Codex access tokens are short-lived; a conservative
# value keeps the broker cache honest without over-trusting an opaque token.
CODEX_DEFAULT_EXPIRES_IN = 3600


def _decode_jwt_payload(token: str) -> dict:
    """Return a JWT's payload claims without verifying the signature, or {} on any failure.

    The account id is a non-secret routing identifier carried in the access
    token; we only need to read it, exactly as the Hermes codex client does
    (raw base64url of the second segment, no key, no verification).
    """
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1]
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}


def _account_id_from_access_token(access_token: str) -> str | None:
    """Extract `chatgpt_account_id` from the access-token JWT, or None if absent.

    Claim path matches the Hermes codex client: nested under the
    `https://api.openai.com/auth` namespace claim.
    """
    claims = _decode_jwt_payload(access_token)
    auth_claim = claims.get("https://api.openai.com/auth")
    if not isinstance(auth_claim, dict):
        return None
    account_id = auth_claim.get("chatgpt_account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def _expires_in_from_access_token(access_token: str, fallback: int) -> int:
    """Derive seconds-until-expiry from the JWT `exp`, falling back when unreadable.

    The broker caches the minted access token for this many seconds, so an
    accurate value (the token's own `exp`) is preferable to the OAuth
    response's `expires_in`, which Codex may omit.
    """
    claims = _decode_jwt_payload(access_token)
    exp = claims.get("exp")
    if isinstance(exp, (int, float)):
        remaining = int(exp - time.time())
        if remaining > 0:
            return remaining
    return fallback


def _exchange_refresh_token(refresh_token: str) -> provider_common.ExchangeResult:
    """POST grant_type=refresh_token to OpenAI; classify revoked / error / response.

    Mirrors `provider_google._exchange_refresh_token`: 400 invalid_grant means
    the refresh_token is permanently unusable (caller deletes the row); any
    other non-200 / network / non-JSON failure is transient.
    """
    try:
        response = httpx.post(
            CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
            timeout=CODEX_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
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

    # OpenAI's token endpoint returns errors in a NESTED shape:
    #   {"error": {"message": "...", "type": "...", "code": "token_expired"}}
    # which differs from the flat OAuth2 {"error": "invalid_grant"} convention.
    # Normalize both: pull a string error code whether `error` is a dict or a str.
    err = body.get("error")
    if isinstance(err, dict):
        err_code = str(err.get("code") or err.get("type") or "")
    else:
        err_code = str(err or "")

    # A refresh token OpenAI rejects as permanently unusable → revoked (caller
    # deletes the row, user must reconnect). Covers the flat `invalid_grant`
    # and OpenAI's nested `token_expired`/`invalid_grant` on 400/401.
    revoking_codes = {"invalid_grant", "token_expired", "invalid_token"}
    if response.status_code in (400, 401) and err_code in revoking_codes:
        return provider_common.ExchangeResult(response=None, revoked=True, error=None)

    return provider_common.ExchangeResult(
        response=None, revoked=False,
        error=f"http {response.status_code}: {err_code or 'unknown'}",
    )


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one (env, owner, app).

    Returns a `provider_common` outcome dict (`has_token | absent | transient`).
    `has_token` carries two secrets — `access_token` and `chatgpt_account_id` —
    both of which the broker injects as headers. A token we can't derive an
    account id from is unusable against chatgpt.com, so we surface `transient`
    (and log loudly) rather than cache a half-usable credential.
    """
    integration = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.OPENAI_CODEX,
    ).first()
    if integration is None:
        return provider_common.absent()

    refresh_token = integration.credentials.get("refresh_token", "")
    if not refresh_token:
        logger.error(
            "codex token refresh: row missing refresh_token env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        return provider_common.absent()

    exchange_result = _exchange_refresh_token(refresh_token=refresh_token)
    if exchange_result.revoked:
        logger.info(
            "codex token refresh: revoked by openai, deleting row env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        integration.delete()
        return provider_common.absent()
    if exchange_result.error is not None:
        logger.error(
            "codex token refresh failed env=%s owner=%s app=%s error=%s",
            environment.slug, owner_user.username, app_slug, exchange_result.error,
        )
        return provider_common.transient()

    access_token = exchange_result.response.get("access_token", "")
    if not access_token:
        logger.error(
            "codex token refresh: response missing access_token env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        return provider_common.transient()

    account_id = _account_id_from_access_token(access_token)
    if account_id is None:
        logger.error(
            "codex token refresh: access_token carries no chatgpt_account_id env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        return provider_common.transient()

    # OpenAI rotates the refresh_token on each grant; persist the new one or the
    # next refresh fails. Account id is stable per account but stored for display.
    new_refresh = exchange_result.response.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        integration.credentials = {**integration.credentials, "refresh_token": new_refresh}
    integration.metadata = {**integration.metadata, "chatgpt_account_id": account_id}
    integration.last_refreshed_at = provider_common.now()
    integration.save(update_fields=["credentials", "metadata", "last_refreshed_at", "updated_at"])

    return provider_common.has_token(
        secrets={"access_token": access_token, "chatgpt_account_id": account_id},
        expires_in=_expires_in_from_access_token(access_token, fallback=CODEX_DEFAULT_EXPIRES_IN),
        config={},
        metadata={},
    )


def revoke(refresh_token: str) -> None:
    """No-op: OpenAI's ChatGPT OAuth client exposes no token-revocation endpoint.

    Disconnect deletes the DOH-side row, which is the load-bearing step; there
    is no upstream revoke to call for this public client. Kept for the OAuth
    provider interface (the unified disconnect handler calls `revoke` for every
    OAuth provider).
    """
    return
