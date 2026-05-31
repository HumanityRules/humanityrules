"""Telegram vault provider: schema, credential validation, and refresh outcome.

Telegram is a paste-style vault provider: the operator pastes a BotFather
bot token and optional allowed user IDs. Registered into the generic vault
surface via `provider_registry`; the batched refresh endpoint
(`token_refresh_batch.py`) calls `refresh_outcome`. Exposes the uniform
vault-provider interface (`schema`, `save_credentials`, `refresh_outcome`)
shared with `provider_slack`.
"""

import logging
import re

import httpx
from django.utils import timezone

from devopshero_app.models import Environment, IntegrationUserCredential, User
from devopshero_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


TELEGRAM_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
TELEGRAM_GET_ME_TIMEOUT_SECONDS = 20
# Telegram bot tokens never expire on the provider side, so this is purely
# the broker's cache lifetime.
TELEGRAM_BROKER_CACHE_SECONDS = 60 * 60
TELEGRAM_INVALID_TOKEN_MESSAGE = "Telegram rejected this bot token. Check that you pasted the complete token from BotFather."


def schema(existing: IntegrationUserCredential | None) -> dict:
    """Build the generic paste-form schema for Telegram."""
    allowed_users = []
    secret_configured = False
    metadata = {}
    if existing is not None:
        allowed_users = existing.config.get("allowed_users", [])
        secret_configured = bool(existing.credentials.get("bot_token"))
        metadata = existing.metadata
    return {
        "provider": "telegram",
        "label": "Telegram",
        "status": "connected" if existing is not None else "not_connected",
        "secret_configured": secret_configured,
        "metadata": metadata,
        "message": "Credentials are sent directly to the DevOps Hero vault. Your Hermes agent does not receive or store them.",
        "restart_required_after_save": True,
        "fields": [
            {
                "name": "bot_token",
                "label": "Bot token",
                "kind": "secret",
                "required": existing is None,
                "placeholder": "123456789:AA...",
                "help": "Paste the token from BotFather. Leave blank to keep the current token.",
            },
            {
                "name": "allowed_users",
                "label": "Allowed Telegram user IDs",
                "kind": "textarea",
                "required": False,
                "value": "\n".join(allowed_users),
                "help": "One numeric Telegram user ID per line, or comma-separated.",
            },
        ],
    }


def _normalize_telegram_allowed_users(value: object) -> tuple[list[str] | None, str | None]:
    """Normalize Telegram user IDs from a textarea/string/list field."""
    if value is None:
        return [], None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value]
    elif isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[\s,]+", value) if part.strip()]
    else:
        return None, "allowed_users must be a string or list"
    for part in parts:
        if not part.isdigit():
            return None, "allowed_users must contain numeric Telegram user IDs only"
    return parts, None


def _telegram_get_me(bot_token: str) -> tuple[dict | None, str | None]:
    """Validate a Telegram bot token and return the bot identity."""
    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=TELEGRAM_GET_ME_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("telegram validation request failed: %s", exc.__class__.__name__)
        return None, "Telegram validation failed. Please try again."
    try:
        body = response.json()
    except ValueError:
        return None, "Telegram validation returned a non-JSON response"
    if response.status_code != 200 or body.get("ok") is not True:
        description = body.get("description")
        logger.error(
            "telegram getMe rejected token: status=%d description=%r",
            response.status_code,
            description,
        )
        if response.status_code == 401 or description == "Unauthorized":
            return None, TELEGRAM_INVALID_TOKEN_MESSAGE
        if isinstance(description, str) and description:
            return None, f"Telegram rejected this bot token: {description}"
        return None, "Telegram rejected this bot token."
    result = body.get("result")
    if not isinstance(result, dict) or result.get("is_bot") is not True:
        logger.error("telegram getMe returned a non-bot result: %r", result)
        return None, "Telegram token did not resolve to a bot"
    return result, None


def save_credentials(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    credentials_payload: dict,
    config_payload: dict,
) -> tuple[IntegrationUserCredential | None, str | None]:
    """Validate and persist Telegram credential/config fields."""
    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TELEGRAM,
    ).first()
    submitted_token = str(credentials_payload.get("bot_token", "") or "").strip()
    existing_token = existing.credentials.get("bot_token", "") if existing is not None else ""
    bot_token = submitted_token or existing_token
    if not bot_token:
        return None, "bot_token is required"
    if submitted_token and TELEGRAM_TOKEN_RE.match(submitted_token) is None:
        return None, "bot_token does not look like a Telegram bot token"

    allowed_users, allowed_users_error = _normalize_telegram_allowed_users(
        value=config_payload.get("allowed_users"),
    )
    if allowed_users_error is not None:
        return None, allowed_users_error

    bot_identity, telegram_error = _telegram_get_me(bot_token=bot_token)
    if telegram_error is not None:
        return None, telegram_error

    metadata = {
        "bot_id": bot_identity.get("id"),
        "bot_username": bot_identity.get("username", ""),
        "bot_name": bot_identity.get("first_name", ""),
        "validated_at": timezone.now().isoformat(),
    }
    credential, _ = IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TELEGRAM,
        defaults={
            "credentials": {"bot_token": bot_token},
            "config": {"allowed_users": allowed_users},
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    return credential, None


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for Telegram (no upstream exchange).

    Telegram bot tokens never expire on the provider side, so this is a
    plain DB read. `expires_in` is the broker's cache lifetime, not a
    Telegram-imposed deadline.
    """
    credential = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TELEGRAM,
    ).first()
    if credential is None:
        return provider_common.absent()
    bot_token = credential.credentials.get("bot_token", "")
    if not bot_token:
        return provider_common.absent()
    return provider_common.has_token(
        secrets={"bot_token": bot_token},
        expires_in=TELEGRAM_BROKER_CACHE_SECONDS,
        config=credential.config,
        metadata=credential.metadata,
    )
