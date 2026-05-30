"""Slack vault helpers: schema, credential validation, and refresh outcome.

Slack is a paste-style vault provider like Telegram, but with two secrets
(an app-level token `xapp-` for the Socket Mode handshake and a bot token
`xoxb-` for the Web API) and a richer setup flow (a Personal/Company-wide
mode selector plus a manifest prefill link the operator uses to create the
Slack app). Registered into the generic vault surface via the dispatch
tables in `user_credential_vault.py`; the batched refresh endpoint
(`token_refresh_batch.py`) calls `refresh_slack_outcome`.

See docs/slack_integration_design.md for the ownership/privacy model.
"""

import logging
import re

import httpx
from django.utils import timezone

from devopshero_app.models import Environment, IntegrationUserCredential, User

logger = logging.getLogger(__name__)


SLACK_API_BASE = "https://slack.com/api"
SLACK_VALIDATION_TIMEOUT_SECONDS = 20
# Slack tokens never expire on the provider side (Socket Mode app token and
# bot token are long-lived), so this is purely the broker's cache lifetime.
SLACK_BROKER_CACHE_SECONDS = 60 * 60

BOT_TOKEN_RE = re.compile(r"^xoxb-[A-Za-z0-9-]+$")
APP_TOKEN_RE = re.compile(r"^xapp-[A-Za-z0-9-]+$")

# Mode keys stored in `config["workspace_scope"]`.
MODE_COMPANY_WIDE = "company_wide"
MODE_PERSONAL = "personal"

# Only company-wide is wired end to end today; personal mode is defined in
# the schema/manifest but offered as disabled in the UI until its owner-only
# allowlist + email→user_id resolution path is finished (Slice 4+).
_ENABLED_MODES = frozenset({MODE_COMPANY_WIDE})


def _slack_manifest(mode: str) -> dict:
    """Build the Slack app manifest for one mode (drives the prefill URL).

    Both modes enable Socket Mode (so the operator can generate an app-level
    token). They differ in event subscriptions and scopes so the privacy
    model is structural: company-wide can't be DMed (no `message.im`),
    personal can't be @-mentioned in channels (no `app_mention`).
    """
    if mode == MODE_PERSONAL:
        bot_scopes = ["chat:write", "im:history", "users:read.email"]
        bot_events = ["message.im"]
    else:
        bot_scopes = ["app_mentions:read", "chat:write", "channels:history"]
        bot_events = ["app_mention"]
    return {
        "display_information": {"name": "DevOps Hero"},
        "features": {"bot_user": {"display_name": "devopshero", "always_online": True}},
        "oauth_config": {"scopes": {"bot": bot_scopes}},
        "settings": {
            "socket_mode_enabled": True,
            "event_subscriptions": {"bot_events": bot_events},
        },
    }


def slack_schema(existing: IntegrationUserCredential | None) -> dict:
    """Build the Slack setup schema consumed by the custom WebUI renderer."""
    mode = MODE_COMPANY_WIDE
    metadata = {}
    if existing is not None:
        mode = existing.config.get("workspace_scope", MODE_COMPANY_WIDE)
        metadata = existing.metadata
    return {
        "provider": "slack",
        "label": "Slack",
        "status": "connected" if existing is not None else "not_connected",
        "metadata": metadata,
        "message": "Tokens are sent directly to the DevOps Hero vault. Your Hermes agent never receives or stores them.",
        "restart_required_after_save": True,
        "selected_mode": mode,
        "modes": [
            {"value": MODE_COMPANY_WIDE, "label": "Company-wide (shared bot in channels)", "enabled": MODE_COMPANY_WIDE in _ENABLED_MODES},
            {"value": MODE_PERSONAL, "label": "Personal (your DMs only)", "enabled": MODE_PERSONAL in _ENABLED_MODES},
        ],
        # The renderer URL-encodes the chosen mode's manifest into
        # https://api.slack.com/apps?new_app=1&manifest_json=<...>
        "manifests": {MODE_COMPANY_WIDE: _slack_manifest(mode=MODE_COMPANY_WIDE), MODE_PERSONAL: _slack_manifest(mode=MODE_PERSONAL)},
        "fields": [
            {"name": "app_token", "label": "App-level token (xapp-)", "kind": "secret", "required": existing is None, "placeholder": "xapp-..."},
            {"name": "bot_token", "label": "Bot token (xoxb-)", "kind": "secret", "required": existing is None, "placeholder": "xoxb-..."},
        ],
    }


def _slack_api_post(method: str, token: str) -> tuple[dict | None, str | None]:
    """POST to one Slack Web API method with a Bearer token; return (body, error)."""
    try:
        response = httpx.post(
            f"{SLACK_API_BASE}/{method}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=SLACK_VALIDATION_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("slack %s request failed: %s", method, exc.__class__.__name__)
        return None, "Slack validation failed. Please try again."
    try:
        body = response.json()
    except ValueError:
        return None, f"Slack {method} returned a non-JSON response"
    if not isinstance(body, dict) or body.get("ok") is not True:
        error = body.get("error") if isinstance(body, dict) else None
        logger.error("slack %s rejected token: error=%r", method, error)
        return None, f"Slack rejected this token: {error or 'unknown error'}"
    return body, None


def _validate_bot_token(bot_token: str) -> tuple[dict | None, str | None]:
    """Validate a bot token via auth.test; return (identity, error)."""
    if BOT_TOKEN_RE.match(bot_token) is None:
        return None, "bot_token does not look like a Slack bot token (expected xoxb-…)"
    return _slack_api_post(method="auth.test", token=bot_token)


def _validate_app_token(app_token: str) -> tuple[None, str | None]:
    """Validate an app-level token by opening (and discarding) a Socket Mode URL."""
    if APP_TOKEN_RE.match(app_token) is None:
        return None, "app_token does not look like a Slack app-level token (expected xapp-…)"
    _body, error = _slack_api_post(method="apps.connections.open", token=app_token)
    return None, error


def save_slack_credentials(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    credentials_payload: dict,
    config_payload: dict,
) -> tuple[IntegrationUserCredential | None, str | None]:
    """Validate and persist Slack app/bot tokens for one logical app."""
    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.SLACK,
    ).first()

    mode = config_payload.get("workspace_scope", MODE_COMPANY_WIDE)
    if mode not in _ENABLED_MODES:
        return None, f"workspace_scope {mode!r} is not available yet"

    submitted_bot = str(credentials_payload.get("bot_token", "") or "").strip()
    submitted_app = str(credentials_payload.get("app_token", "") or "").strip()
    existing_creds = existing.credentials if existing is not None else {}
    bot_token = submitted_bot or existing_creds.get("bot_token", "")
    app_token = submitted_app or existing_creds.get("app_token", "")
    if not bot_token or not app_token:
        return None, "both app_token and bot_token are required"

    # Only validate freshly-submitted tokens; an unchanged token kept from the
    # existing row was already validated when it was first saved.
    bot_identity = {}
    if submitted_bot:
        bot_identity, error = _validate_bot_token(bot_token=bot_token)
        if error is not None:
            return None, error
    if submitted_app:
        _identity, error = _validate_app_token(app_token=app_token)
        if error is not None:
            return None, error

    config = {"workspace_scope": mode}
    metadata = existing.metadata if existing is not None else {}
    if bot_identity:
        metadata = {
            "team": bot_identity.get("team", ""),
            "team_id": bot_identity.get("team_id", ""),
            "bot_user_id": bot_identity.get("user_id", ""),
            "validated_at": timezone.now().isoformat(),
        }

    credential, _ = IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.SLACK,
        defaults={
            "credentials": {"app_token": app_token, "bot_token": bot_token},
            "config": config,
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    return credential, None


def refresh_slack_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for Slack (plain DB read).

    Returns both secrets so the broker can inject the app token on the Socket
    Mode handshake and the bot token on Web API calls. `expires_in` is the
    broker cache lifetime, not a Slack-imposed deadline.
    """
    credential = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.SLACK,
    ).first()
    if credential is None:
        return {"outcome": "absent"}
    app_token = credential.credentials.get("app_token", "")
    bot_token = credential.credentials.get("bot_token", "")
    if not app_token or not bot_token:
        return {"outcome": "absent"}
    return {
        "outcome": "has_token",
        "secrets": {"app_token": app_token, "bot_token": bot_token},
        "expires_in": SLACK_BROKER_CACHE_SECONDS,
        "config": credential.config,
        "metadata": credential.metadata,
    }
