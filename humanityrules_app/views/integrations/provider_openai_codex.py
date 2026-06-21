"""OpenAI Codex (ChatGPT-subscription) per-user integration: device-flow connect + token refresh.

Unlike the redirect-dance OAuth providers (Google, GitHub), Codex connects via
OpenAI's device flow run *by the env-resident broker* (see
`doh_runtime/integrations/integrations_broker.py`). The broker drives the device handshake,
then POSTs the resulting refresh_token to DOH at
`/api/integrations/credentials/openai-codex/device-complete`, which stores it
as an IntegrationUserCredential row. No browser redirect, no callback of ours,
no DOH-held client secret — Codex's client is a public OAuth client.

Refresh (`refresh_outcome`): exchanges the stored refresh_token at
`auth.openai.com/oauth/token` (grant_type=refresh_token, public client_id, no
secret) for a fresh access token, derives the `chatgpt_account_id` claim from
the access-token JWT, and returns BOTH as broker secrets. The broker injects
`Authorization: Bearer <access_token>` and `ChatGPT-Account-ID: <account_id>`
on the wire to chatgpt.com (credential model A1: the account id never enters
the sandbox). If OpenAI has revoked the refresh_token the row is deleted and
the outcome flips to `absent`.

See `docs/integrations/device_flow_integration_design.md`.
"""

import logging

import httpx

from humanityrules_app.models import Environment, IntegrationUserCredential, User
from humanityrules_app.views.integrations import provider_common

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


def _account_id_from_access_token(access_token: str) -> str | None:
    """Extract `chatgpt_account_id` from the access-token JWT, or None if absent.

    Claim path matches the Hermes codex client: nested under the
    `https://api.openai.com/auth` namespace claim.
    """
    claims = provider_common.decode_jwt_payload(token=access_token)
    auth_claim = claims.get("https://api.openai.com/auth")
    if not isinstance(auth_claim, dict):
        return None
    account_id = auth_claim.get("chatgpt_account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def _error_code_of(body: dict) -> str:
    """Pull a string error code from OpenAI's flat-or-nested error body.

    OpenAI's token endpoint returns errors in a NESTED shape:
    `{"error": {"message": ..., "type": ..., "code": "token_expired"}}`, which
    differs from the flat OAuth2 `{"error": "invalid_grant"}` convention.
    """
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("code") or err.get("type") or "")
    return str(err or "")


def _exchange_refresh_token(refresh_token: str) -> provider_common.ExchangeResult:
    """POST grant_type=refresh_token to OpenAI; classify revoked / error / response.

    A refresh token OpenAI rejects as permanently unusable → revoked (caller
    deletes the row, user must reconnect); any other non-200 / network /
    non-JSON failure is transient.
    """
    return provider_common.exchange_refresh_token(
        send=lambda: httpx.post(
            CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
            timeout=CODEX_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
        ),
        revoking_statuses=frozenset({400, 401}),
        revoking_error_codes=frozenset({"invalid_grant", "token_expired", "invalid_token"}),
        error_code_of=_error_code_of,
    )


def store_device_credentials(environment: Environment, owner_user: User, app_slug: str, payload: dict) -> tuple[int, dict]:
    """Store the refresh/access token pair the broker obtained from OpenAI's device flow."""
    refresh_token = payload.get("refresh_token")
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return 400, {"error": "access_token is required"}

    # The broker already exchanged the device authorization_code for this
    # refresh/access pair, so we do NOT re-mint here: re-minting would rotate
    # the refresh token (invalidating the one we were handed) and add a fragile
    # second OpenAI round-trip on connect. We only need the chatgpt_account_id,
    # which is a claim in the access token the broker forwarded — derive it
    # directly. If it's absent, this isn't a usable ChatGPT-subscription login.
    account_id = _account_id_from_access_token(access_token=access_token)
    if account_id is None:
        return 400, {"error": "token has no chatgpt_account_id; not a ChatGPT-subscription login"}

    return provider_common.store_oauth_refresh_credential(
        provider=IntegrationUserCredential.Provider.OPENAI_CODEX,
        environment=environment,
        owner_user=owner_user,
        app_slug=app_slug,
        refresh_token=refresh_token,
        metadata={"chatgpt_account_id": account_id},
    )


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one (env, owner, app).

    Returns a `provider_common` outcome dict (`has_token | absent | transient`).
    `has_token` carries two secrets — `access_token` and `chatgpt_account_id` —
    both of which the broker injects as headers. A token we can't derive an
    account id from is unusable against chatgpt.com, so we surface `transient`
    (and log loudly) rather than cache a half-usable credential.
    """
    def build_secrets(access_token: str, response: dict) -> provider_common.RefreshSecrets | None:
        account_id = _account_id_from_access_token(access_token)
        if account_id is None:
            logger.error(
                "codex token refresh: access_token carries no chatgpt_account_id env=%s owner=%s app=%s",
                environment.slug, owner_user.username, app_slug,
            )
            return None
        return provider_common.RefreshSecrets(
            secrets={"access_token": access_token, "chatgpt_account_id": account_id},
            expires_in=provider_common.expires_in_from_access_token(access_token=access_token, fallback=CODEX_DEFAULT_EXPIRES_IN),
            row_metadata={"chatgpt_account_id": account_id},
        )

    return provider_common.run_refresh_exchange(
        provider=IntegrationUserCredential.Provider.OPENAI_CODEX,
        logger=logger,
        environment=environment,
        owner_user=owner_user,
        app_slug=app_slug,
        exchange=_exchange_refresh_token,
        build_secrets=build_secrets,
    )


def revoke(refresh_token: str) -> None:
    """No-op: OpenAI's ChatGPT OAuth client exposes no token-revocation endpoint.

    Disconnect deletes the DOH-side row, which is the load-bearing step; there
    is no upstream revoke to call for this public client. Kept for the OAuth
    provider interface (the unified disconnect handler calls `revoke` for every
    OAuth provider).
    """
    return
