"""Telegram managed-bot provider: link+poll connect via Telegram's Bot Management mode.

Telegram bots are no longer connected by pasting a BotFather token. DOH owns a
manager bot (BotFather "Bot Management Mode" — see settings
`TELEGRAM_MANAGER_BOT_TOKEN` / `TELEGRAM_MANAGER_BOT_USERNAME`); `schema()`
returns a `https://t.me/newbot/{manager}/{suggested_username}` deep link the
user opens to create their own bot in one tap. While the WebUI polls the vault
poll endpoint, `poll_setup` scans the manager bot's `managed_bot` updates for
the suggested username, fetches the new bot's token with `getManagedBotToken`,
and persists it. Registered into the generic vault surface via
`provider_registry`; the broker-facing refresh contract is unchanged.
"""

import logging
import re
import secrets
from urllib.parse import quote_plus

import httpx
import segno
from django.conf import settings
from django.utils import timezone

from humanityrules_app.models import App, Environment, IntegrationUserCredential, User
from humanityrules_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


# Manager-bot API calls happen only during a connect session (browser poll
# path); the broker's refresh path never talks to Telegram.
TELEGRAM_API_TIMEOUT_SECONDS = 5
# Telegram bot tokens never expire on the provider side, so this is purely
# the broker's cache lifetime.
TELEGRAM_BROKER_CACHE_SECONDS = 60 * 60
TELEGRAM_NOT_CONFIGURED_MESSAGE = "Telegram is not configured on this DevOps Hero deployment: the manager bot credentials are missing."
TELEGRAM_TOKEN_FETCH_FAILED_MESSAGE = "Telegram did not return your new bot's token. Please try again."
# Telegram bot usernames are 5-32 chars of [A-Za-z0-9_] and must end in "bot";
# the random suffix plus "_bot" leaves this much room for the app-slug prefix.
_USERNAME_PREFIX_MAX_LEN = 20
TELEGRAM_BOT_NAME_MAX_LEN = 64


def _manager_bot_configured() -> bool:
    """Return whether DOH's Telegram manager bot credentials are configured."""
    return bool(settings.TELEGRAM_MANAGER_BOT_TOKEN and settings.TELEGRAM_MANAGER_BOT_USERNAME)


def _manager_bot_api(method: str, params: dict) -> tuple[object | None, str | None]:
    """Call a Bot API method as the manager bot; return (result, error)."""
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_MANAGER_BOT_TOKEN}/{method}",
            json=params,
            timeout=TELEGRAM_API_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("telegram manager bot %s request failed: %s", method, exc.__class__.__name__)
        return None, "network error"
    try:
        body = response.json()
    except ValueError:
        logger.error("telegram manager bot %s returned non-JSON (status=%d)", method, response.status_code)
        return None, "non-JSON response"
    if response.status_code != 200 or body.get("ok") is not True:
        description = body.get("description")
        logger.error(
            "telegram manager bot %s rejected: status=%d description=%r",
            method,
            response.status_code,
            description,
        )
        return None, str(description) if description else f"http {response.status_code}"
    return body.get("result"), None


def _suggest_bot_username(app_slug: str) -> str:
    """Build a unique, Telegram-legal suggested username for the user's new bot.

    The random suffix is the correlation key between this setup session and
    the `managed_bot` update Telegram sends once the user confirms creation.
    """
    prefix = re.sub(r"[^a-z0-9_]+", "_", app_slug.lower()).strip("_")[:_USERNAME_PREFIX_MAX_LEN].strip("_")
    if not prefix or not prefix[0].isalpha():
        prefix = f"doh_{prefix}".strip("_")
    return f"{prefix}_{secrets.token_hex(3)}_bot"


def schema(existing: IntegrationUserCredential | None, app: App | None, owner_user: User | None) -> dict:
    """Build the link+poll connect schema, or the config-only form when connected.

    Not-connected: `mode=link_poll` with the bot-creation deep link;
    `signed_state` is moved into the signed setup token by the setup-session
    view so the poll endpoint can correlate the `managed_bot` update.
    Connected: a plain form for `allowed_users` (the token is managed, never
    edited by hand). `owner_user` is unused (Slack-only).
    """
    if existing is not None:
        return {
            "provider": "telegram",
            "label": "Telegram",
            "status": "connected",
            "mode": "form",
            "secret_configured": bool(existing.credentials.get("bot_token")),
            "metadata": existing.metadata,
            "message": "Your Telegram bot is managed by DevOps Hero. Configure who is allowed to talk to it.",
            "restart_required_after_save": True,
            "fields": [
                {
                    "name": "allowed_users",
                    "label": "Allowed Telegram user IDs",
                    "kind": "textarea",
                    "required": False,
                    "value": "\n".join(existing.config.get("allowed_users", [])),
                    "help": "One numeric Telegram user ID per line, or comma-separated. You were added automatically when you created the bot.",
                },
            ],
        }
    if not _manager_bot_configured():
        return {
            "provider": "telegram",
            "label": "Telegram",
            "status": "not_connected",
            "mode": "link_poll",
            "message": TELEGRAM_NOT_CONFIGURED_MESSAGE,
            "fields": [],
        }
    suggested_username = _suggest_bot_username(app_slug=app.slug if app is not None else "agent")
    bot_name = (app.name if app is not None else "Hermes Agent").strip()[:TELEGRAM_BOT_NAME_MAX_LEN]
    link_url = (
        f"https://t.me/newbot/{settings.TELEGRAM_MANAGER_BOT_USERNAME}/{suggested_username}"
        f"?name={quote_plus(bot_name)}"
    )
    return {
        "provider": "telegram",
        "label": "Telegram",
        "status": "not_connected",
        "mode": "link_poll",
        "message": (
            "DevOps Hero creates a Telegram bot."
            "Scan the QR code with your phone and confirm the bot in Telegram."
        ),
        "qr_data_uri": _link_qr_data_uri(link_url=link_url),
        "qr_caption": "Scan with your phone’s camera or Telegram app",
        "link_note": f"Keep the suggested @{suggested_username} username — it is how DevOps Hero recognizes your new bot.",
        "pending_message": "Waiting for you to confirm in Telegram…",
        "restart_required_after_save": True,
        "signed_state": {"bot_username": suggested_username},
        "fields": [],
    }


def _link_qr_data_uri(link_url: str) -> str:
    """Render the bot-creation link as an SVG QR data URI for the connect modal.

    Generated on DOH so the WebUI extension stays dependency-free. Explicit
    light background: the modal is dark-themed and a transparent QR would be
    unscannable there.
    """
    return segno.make(link_url, error="m").svg_data_uri(dark="#000", light="#fff", border=2)


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


def _find_managed_bot_creation(expected_username: str) -> dict | None:
    """Scan the manager bot's pending `managed_bot` updates for *expected_username*.

    Stateless on purpose: `getUpdates` is called without an offset, so the
    updates are never confirmed and concurrent connect sessions cannot consume
    each other's events. Telegram keeps unconfirmed updates for 24h — far
    longer than a setup session. Returns the most recent matching
    `ManagedBotUpdated` dict, or None (no match yet, or a transient API
    failure — both mean "keep polling").
    """
    result, error = _manager_bot_api(
        method="getUpdates",
        params={"timeout": 0, "allowed_updates": ["managed_bot"]},
    )
    if error is not None or not isinstance(result, list):
        return None
    match = None
    for update in result:
        managed = update.get("managed_bot") if isinstance(update, dict) else None
        if not isinstance(managed, dict):
            continue
        bot = managed.get("bot")
        if not isinstance(bot, dict):
            continue
        if str(bot.get("username", "")).lower() == expected_username.lower():
            match = managed
    return match


def poll_setup(owner_user: User, environment: Environment, app_slug: str, state: dict) -> tuple[dict | None, str | None]:
    """Check whether the user's managed bot exists yet; fetch and store its token when it does.

    Called repeatedly by the vault poll endpoint with the `signed_state` that
    `schema()` bound into the setup token. The bot's creator is auto-added to
    `allowed_users` so the new bot answers them immediately.
    """
    if not _manager_bot_configured():
        return None, TELEGRAM_NOT_CONFIGURED_MESSAGE
    expected_username = str(state.get("bot_username", "") or "")
    if not expected_username:
        return None, "This setup session is missing its bot username. Close this dialog and click Connect again."

    managed = _find_managed_bot_creation(expected_username=expected_username)
    if managed is None:
        return {"status": "pending"}, None
    bot = managed["bot"]
    creator = managed.get("user") if isinstance(managed.get("user"), dict) else {}

    token_result, token_error = _manager_bot_api(method="getManagedBotToken", params={"user_id": bot["id"]})
    if token_error is not None or not isinstance(token_result, str) or not token_result:
        return None, TELEGRAM_TOKEN_FETCH_FAILED_MESSAGE

    allowed_users = [str(creator["id"])] if creator.get("id") is not None else []
    metadata = {
        "bot_id": bot.get("id"),
        "bot_username": bot.get("username", ""),
        "bot_name": bot.get("first_name", ""),
        "creator_telegram_id": creator.get("id"),
        "creator_telegram_username": creator.get("username", ""),
        "validated_at": timezone.now().isoformat(),
    }
    credential, _created = IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TELEGRAM,
        defaults={
            "credentials": {"bot_token": token_result},
            "config": {"allowed_users": allowed_users},
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    logger.info(
        "telegram managed bot connected: bot=@%s owner=%s env=%s app=%s",
        metadata["bot_username"],
        owner_user.username,
        environment.slug,
        app_slug,
    )
    return {"status": "connected", "metadata": credential.metadata, "restart_required": True}, None


def save_credentials(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    credentials_payload: dict,
    config_payload: dict,
) -> tuple[IntegrationUserCredential | None, str | None]:
    """Update Telegram config (allowed users) — the bot token is managed, never pasted."""
    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TELEGRAM,
    ).first()
    if existing is None:
        return None, "Telegram is not connected. Use Connect to create your bot first."
    allowed_users, allowed_users_error = _normalize_telegram_allowed_users(
        value=config_payload.get("allowed_users"),
    )
    if allowed_users_error is not None:
        return None, allowed_users_error
    existing.config = {**existing.config, "allowed_users": allowed_users}
    existing.save(update_fields=["config", "updated_at"])
    return existing, None


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for Telegram (plain DB read).

    Telegram tokens never expire on the provider side, so `expires_in` is
    purely the broker's cache lifetime. If the user ever revokes the bot's
    token via BotFather, recovery is a reconnect — we deliberately don't
    re-fetch the live token here, which keeps Telegram's API out of the
    broker's batched refresh path.
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
