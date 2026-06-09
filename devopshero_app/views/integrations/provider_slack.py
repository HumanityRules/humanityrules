"""Slack vault provider: schema, credential validation, and refresh outcome.

Slack is a paste-style vault provider, but with two secrets
(an app-level token `xapp-` for the Socket Mode handshake and a bot token
`xoxb-` for the Web API) and a richer setup flow (a Personal/Company-wide
mode selector plus a manifest prefill link the operator uses to create the
Slack app). Registered into the generic vault surface via `provider_registry`;
the batched refresh endpoint (`token_refresh_batch.py`) calls `refresh_outcome`.
Exposes the uniform vault-provider interface (`schema`, `save_credentials`,
`refresh_outcome`) shared with `provider_telegram`.

See docs/slack_integration_design.md for the ownership/privacy model.
"""

import logging
import re

import httpx
from django.utils import timezone

from devopshero_app.models import App, Environment, IntegrationUserCredential, User
from devopshero_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


SLACK_API_BASE = "https://slack.com/api"
SLACK_VALIDATION_TIMEOUT_SECONDS = 20
# Slack tokens never expire on the provider side (Socket Mode app token and
# bot token are long-lived), so this is purely the broker's cache lifetime.
SLACK_BROKER_CACHE_SECONDS = 60 * 60

BOT_TOKEN_RE = re.compile(r"^xoxb-[A-Za-z0-9-]+$")
APP_TOKEN_RE = re.compile(r"^xapp-[A-Za-z0-9-]+$")

# Slack manifest name limits (https://docs.slack.dev/reference/app-manifest):
# display_information.name is <=35 chars (any character); bot_user.display_name
# is <=80 chars restricted to [a-z0-9._-]. The operator's single "App name"
# entry feeds both, sanitized per field. The default is the deploying app's
# template name (App.source_template.name), set in the DOH control plane.
SLACK_APP_NAME_MAX_LEN = 35
SLACK_BOT_NAME_MAX_LEN = 80
SLACK_DEFAULT_APP_NAME = "Slackbot"
SLACK_DEFAULT_BOT_NAME = "slackbot"
_BOT_NAME_DISALLOWED_RE = re.compile(r"[^a-z0-9._-]+")

# Mode keys stored in `config["workspace_scope"]`.
MODE_COMPANY_WIDE = "company_wide"
MODE_PERSONAL = "personal"

# Modes the backend accepts. Both are enabled. Personal mode resolves the
# owner's email to a Slack user_id at save time and stores it as the sole
# SLACK_ALLOWED_USERS entry (see save_credentials); company-wide opts into
# SLACK_ALLOW_ALL_USERS instead.
_ENABLED_MODES = frozenset({MODE_COMPANY_WIDE, MODE_PERSONAL})


def _clean_app_name(raw: str) -> str:
    """Clamp the operator's name to the app-name field (<=35 chars, any chars)."""
    name = raw.strip()[:SLACK_APP_NAME_MAX_LEN].strip()
    return name or SLACK_DEFAULT_APP_NAME


def _clean_bot_name(raw: str) -> str:
    """Derive the bot display name (<=80 chars, [a-z0-9._-]) from the app name."""
    name = _BOT_NAME_DISALLOWED_RE.sub("-", raw.strip().lower()).strip("-")[:SLACK_BOT_NAME_MAX_LEN].strip("-")
    return name or SLACK_DEFAULT_BOT_NAME


def _default_app_name(app: App | None) -> str:
    """Default Slack name: the deploying app's template name, else the fallback."""
    if app is not None and app.source_template is not None:
        return _clean_app_name(app.source_template.name)
    return SLACK_DEFAULT_APP_NAME


def _slack_manifest(mode: str, app_name: str) -> dict:
    """Build the Slack app manifest for one mode (drives the prefill URL).

    Both modes enable Socket Mode (so the operator can generate an app-level
    token). They differ in event subscriptions and scopes so the privacy
    model is structural: company-wide can't be DMed (no `message.im`),
    personal can't be @-mentioned in channels (no `app_mention`).

    `app_name` is the operator-chosen name; it feeds both the app's
    `display_information.name` and the channel-facing `bot_user.display_name`,
    sanitized per Slack's differing field rules.
    """
    if mode == MODE_PERSONAL:
        # users:read.email is an *extension* of users:read; Slack rejects a
        # manifest that requests the extension without its base scope
        # ("Missing bot extension scopes `users:read`"). Both are needed to
        # resolve the owner's email to a user_id via users.lookupByEmail.
        # im:write lets us open the owner's DM channel (conversations.open) at
        # save time and store its D… id as the home channel — see save_credentials.
        bot_scopes = ["chat:write", "im:history", "im:write", "users:read", "users:read.email"]
        bot_events = ["message.im"]
        # A user can only DM the bot if the Messages tab is enabled AND not
        # read-only — Slack's default is tab-on-but-read-only, which hides the
        # compose box, so message.im would never fire. read_only=False is the
        # load-bearing field. Company-wide subscribes to no DMs and omits this.
        app_home = {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False}
    else:
        # `app_mention` alone fires only on messages that explicitly @-mention
        # the bot, so non-mention replies in an active thread never reach the
        # gateway and it can't follow the conversation. `message.channels` /
        # `message.groups` (backed by `channels:history` / `groups:history`)
        # deliver every public- / private-channel message; the gateway's own
        # gating then decides what to answer (mention, mentioned-thread,
        # bot-thread, or active session). The `*:read` scopes are required by
        # `users.conversations`, which the gateway calls to build its channel
        # directory — Slack demands all four regardless of the conversation
        # types requested.
        bot_scopes = [
            "app_mentions:read",
            "chat:write",
            "channels:history",
            "groups:history",
            "channels:read",
            "groups:read",
            "im:read",
            "mpim:read",
        ]
        bot_events = ["app_mention", "message.channels", "message.groups"]
        app_home = None
    features = {"bot_user": {"display_name": _clean_bot_name(app_name), "always_online": True}}
    if app_home is not None:
        features["app_home"] = app_home
    return {
        "display_information": {"name": _clean_app_name(app_name)},
        "features": features,
        "oauth_config": {"scopes": {"bot": bot_scopes}},
        "settings": {
            "socket_mode_enabled": True,
            "event_subscriptions": {"bot_events": bot_events},
        },
    }


def schema(existing: IntegrationUserCredential | None, app: App | None, owner_user: User | None) -> dict:
    """Build the Slack setup schema consumed by the custom WebUI renderer."""
    mode = MODE_COMPANY_WIDE
    metadata = {}
    app_name = _default_app_name(app)
    # Prefill the owner-email field with the deploying user's DOH email so the
    # common case (the owner connecting their own DMs) is one keystroke. Personal
    # mode resolves it to a Slack user_id at save (see save_credentials).
    owner_email = owner_user.email if owner_user is not None else ""
    owner_name = ""
    # Company-wide has no single owner to DM, so the home channel (where the
    # gateway delivers cron/proactive output) can't be auto-resolved like
    # personal mode does — the operator names a channel the bot is invited to.
    # Optional: left blank, the gateway prompts once and they can run !sethome.
    home_channel = ""
    if existing is not None:
        mode = existing.config.get("workspace_scope", MODE_COMPANY_WIDE)
        metadata = existing.metadata
        # Keep the operator's previously-chosen name on reopen; the template
        # default only seeds the first connect.
        app_name = existing.config.get("app_name") or app_name
        home_channel = existing.config.get("home_channel", "") if mode == MODE_COMPANY_WIDE else ""
        owner_name = metadata.get("owner_name", "")
        # An owner is already bound: blank the email field so a name-only
        # reconfigure keeps that owner rather than silently rebinding to the
        # DOH email (which may differ from the bound Slack account).
        if metadata.get("owner_user_id"):
            owner_email = ""
    return {
        "provider": "slack",
        "label": "Slack",
        "status": "connected" if existing is not None else "not_connected",
        "metadata": metadata,
        "message": "Tokens are sent directly to the DevOps Hero vault. Your Hermes agent never receives or stores them.",
        "restart_required_after_save": True,
        "selected_mode": mode,
        "app_name": app_name,
        "app_name_max_len": SLACK_APP_NAME_MAX_LEN,
        "owner_email": owner_email,
        "owner_name": owner_name,
        "home_channel": home_channel,
        "modes": [
            {"value": MODE_COMPANY_WIDE, "label": "Company-wide (shared bot in channels)", "enabled": MODE_COMPANY_WIDE in _ENABLED_MODES},
            {"value": MODE_PERSONAL, "label": "Personal (your DMs only)", "enabled": MODE_PERSONAL in _ENABLED_MODES},
        ],
        # The renderer URL-encodes the chosen mode's manifest into
        # https://api.slack.com/apps?new_app=1&manifest_json=<...>, re-baking the
        # operator's app name into both manifests as it is edited.
        "manifests": {
            MODE_COMPANY_WIDE: _slack_manifest(mode=MODE_COMPANY_WIDE, app_name=app_name),
            MODE_PERSONAL: _slack_manifest(mode=MODE_PERSONAL, app_name=app_name),
        },
        "fields": [
            {"name": "app_token", "label": "App-level token (xapp-)", "kind": "secret", "required": existing is None, "placeholder": "xapp-..."},
            {"name": "bot_token", "label": "Bot token (xoxb-)", "kind": "secret", "required": existing is None, "placeholder": "xoxb-..."},
        ],
    }


# Sentinel error codes for failures that have no Slack `error` string (transport
# fault or non-JSON body), distinct from a real Slack `ok:false` error code.
SLACK_ERROR_REQUEST_FAILED = "request_failed"
SLACK_ERROR_BAD_RESPONSE = "bad_response"


def _slack_transport_error(error_code: str) -> str | None:
    """Return a clean retry message for a transport/bad-response sentinel, else None.

    Real Slack error codes (e.g. `invalid_auth`, `users_not_found`) return None
    so the caller can phrase a code-specific message; the two sentinels mean we
    never reached a Slack verdict, so "rejected" would be a lie.
    """
    if error_code in (SLACK_ERROR_REQUEST_FAILED, SLACK_ERROR_BAD_RESPONSE):
        return "Slack was unreachable or returned an unexpected response. Please try again."
    return None


def _slack_api_post(method: str, token: str, data: dict | None) -> tuple[dict | None, str | None]:
    """POST to one Slack Web API method with a Bearer token; return (body, error_code).

    On failure the second element is the raw Slack `error` code (e.g.
    `users_not_found`, `invalid_auth`) or one of the SLACK_ERROR_* sentinels.
    Callers turn the code into a user-facing message — the same fault means
    different things per method (a bad token vs. an unknown email).
    """
    try:
        response = httpx.post(
            f"{SLACK_API_BASE}/{method}",
            headers={"Authorization": f"Bearer {token}"},
            data=data,
            timeout=SLACK_VALIDATION_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("slack %s request failed: %s", method, exc.__class__.__name__)
        return None, SLACK_ERROR_REQUEST_FAILED
    try:
        body = response.json()
    except ValueError:
        logger.error("slack %s returned a non-JSON response", method)
        return None, SLACK_ERROR_BAD_RESPONSE
    if not isinstance(body, dict) or body.get("ok") is not True:
        error = body.get("error") if isinstance(body, dict) else None
        logger.error("slack %s failed: error=%r", method, error)
        return None, error or SLACK_ERROR_BAD_RESPONSE
    return body, None


def _validate_bot_token(bot_token: str) -> tuple[dict | None, str | None]:
    """Validate a bot token via auth.test; return (identity, error)."""
    if BOT_TOKEN_RE.match(bot_token) is None:
        return None, "bot_token does not look like a Slack bot token (expected xoxb-…)"
    body, error = _slack_api_post(method="auth.test", token=bot_token, data=None)
    if error is not None:
        return None, _slack_transport_error(error) or f"Slack rejected this bot token: {error}"
    return body, None


def _validate_app_token(app_token: str) -> tuple[None, str | None]:
    """Validate an app-level token by opening (and discarding) a Socket Mode URL."""
    if APP_TOKEN_RE.match(app_token) is None:
        return None, "app_token does not look like a Slack app-level token (expected xapp-…)"
    _body, error = _slack_api_post(method="apps.connections.open", token=app_token, data=None)
    if error is not None:
        return None, _slack_transport_error(error) or f"Slack rejected this app-level token: {error}"
    return None, None


def _resolve_owner_user_id(bot_token: str, owner_email: str) -> tuple[dict | None, str | None]:
    """Resolve a personal-mode owner email to a Slack user via users.lookupByEmail.

    Returns the Slack `user` object (carries `id` and the display name) so the
    caller can store the user_id as the allowlist and show the name back. The
    email itself is never persisted — only the resolved user_id.
    """
    email = owner_email.strip()
    if not email:
        return None, "An owner email is required for personal mode."
    body, error = _slack_api_post(method="users.lookupByEmail", token=bot_token, data={"email": email})
    if error is not None:
        transport = _slack_transport_error(error)
        if transport is not None:
            return None, transport
        # users_not_found means the email isn't a member of the bot's workspace
        # — the common operator mistake (wrong address, or not in this Slack).
        if error == "users_not_found":
            return None, f"No Slack user found for {email} in this workspace. Use the email tied to your Slack account."
        # missing_scope means the bot token lacks users:read.email — the signature
        # of a company-wide app token reused for personal mode (its manifest omits
        # that scope). Tell the operator to recreate the app from the personal link.
        if error == "missing_scope":
            return None, "This bot token can't look up users. Create the app from the Personal link above (it adds the needed scope) and paste its new bot token."
        return None, f"Slack could not look up that email: {error}"
    user = body.get("user") if isinstance(body, dict) else None
    if not isinstance(user, dict) or not user.get("id"):
        logger.error("slack users.lookupByEmail returned no user id: body=%r", body)
        return None, "Slack did not return a user for that email."
    return user, None


def _open_owner_dm_channel(bot_token: str, owner_user_id: str) -> tuple[str | None, str | None]:
    """Open the bot↔owner DM via conversations.open; return (channel_id, error).

    The returned `D…` channel id is the personal-mode home channel (where the
    gateway delivers cron output and proactive messages). Passing the owner's
    `U…` id directly to chat.postMessage would land in their Slackbot DM, not
    the bot's, so we resolve the real DM channel here. Needs the `im:write`
    scope the personal manifest carries.
    """
    body, error = _slack_api_post(method="conversations.open", token=bot_token, data={"users": owner_user_id})
    if error is not None:
        transport = _slack_transport_error(error)
        if transport is not None:
            return None, transport
        # missing_scope means the bot token predates the im:write addition — a
        # personal app created before this change. Reinstalling picks it up.
        if error == "missing_scope":
            return None, "This bot token can't open a DM. Recreate the app from the Personal link above (it adds the needed scope) and paste its new bot token."
        return None, f"Slack could not open the owner's DM: {error}"
    channel = body.get("channel") if isinstance(body, dict) else None
    channel_id = channel.get("id") if isinstance(channel, dict) else None
    if not channel_id:
        logger.error("slack conversations.open returned no channel id: body=%r", body)
        return None, "Slack did not return a DM channel for the owner."
    return channel_id, None


def save_credentials(
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

    # A mode is baked into the Slack app's manifest (subscriptions + scopes),
    # not just our env: company-wide subscribes to channel events, personal to
    # message.im. Switching modes therefore needs a *different* Slack app, so we
    # refuse to flip the stored mode against tokens kept from the old app — that
    # would write company-wide env over an app that only emits message.im (or
    # vice versa) and "connect" a bot that silently receives nothing.
    prior_mode = existing.config.get("workspace_scope") if existing is not None else None
    if prior_mode is not None and prior_mode != mode and not (submitted_bot and submitted_app):
        return None, "Switching modes needs a new Slack app. Create it from the link above and paste both new tokens."

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

    # The gateway denies by default; each mode opens access differently.
    # Company-wide: anyone in an invited channel (SLACK_ALLOW_ALL_USERS=true).
    # Personal: owner-only via SLACK_ALLOWED_USERS=<resolved user_id>, no
    # allow-all. Note the gateway's pairing flow can still admit other users
    # (it bypasses this allowlist) — see docs/slack_integration_design.md.
    config = {"workspace_scope": mode, "app_name": _clean_app_name(str(config_payload.get("app_name", "") or ""))}
    metadata = existing.metadata if existing is not None else {}
    if bot_identity:
        metadata = {
            "team": bot_identity.get("team", ""),
            "team_id": bot_identity.get("team_id", ""),
            "bot_user_id": bot_identity.get("user_id", ""),
            "validated_at": timezone.now().isoformat(),
        }

    if mode == MODE_COMPANY_WIDE:
        config["allow_all_users"] = "true"
        # Drop any owner identity left over from a prior personal-mode save, so
        # the connected card doesn't keep showing "Replies only to …".
        metadata = {k: v for k, v in metadata.items() if k not in ("owner_user_id", "owner_name")}
        # Optional operator-named home channel (a C… id the bot is invited to).
        # No validation: the bot isn't a member of any channel until a human
        # invites it, so we can't verify the id here — left blank, the gateway
        # prompts once and the operator can run !sethome instead.
        home_channel = str(config_payload.get("home_channel", "") or "").strip()
        if home_channel:
            config["home_channel"] = home_channel
    else:
        # Resolve only a freshly-submitted email (mirrors the token-freshness
        # rule above): a name-only reconfigure submits a blank email and keeps
        # the bound owner, rather than silently rebinding to a possibly-
        # different account. A fresh bot-token submit rebuilds `metadata`
        # above, so re-merge the owner keys either way.
        submitted_email = str(config_payload.get("owner_email", "") or "").strip()
        # A kept owner_user_id is only valid against the kept bot token's
        # workspace. If the bot token is being replaced, the old user_id may
        # name someone in a different workspace, so a fresh email is required —
        # owner-freshness is coupled to token-freshness.
        if submitted_bot and not submitted_email:
            return None, "Re-enter the owner's Slack email when you change the bot token."
        if submitted_email:
            owner, owner_error = _resolve_owner_user_id(bot_token=bot_token, owner_email=submitted_email)
            if owner_error is not None:
                return None, owner_error
            owner_user_id = owner["id"]
            owner_name = owner.get("real_name") or owner.get("name", "")
            # Resolve the bot↔owner DM now so the gateway has a home channel for
            # cron output and proactive messages — suppresses the first-message
            # "no home channel" prompt. Home-channel freshness follows owner
            # (and thus token) freshness: a fresh email re-resolves the DM.
            home_channel, home_error = _open_owner_dm_channel(bot_token=bot_token, owner_user_id=owner_user_id)
            if home_error is not None:
                return None, home_error
        else:
            existing_metadata = existing.metadata if existing is not None else {}
            owner_user_id = existing_metadata.get("owner_user_id", "")
            owner_name = existing_metadata.get("owner_name", "")
            if not owner_user_id:
                return None, "An owner email is required for personal mode."
            # Owner kept (name-only reconfigure) → keep the DM resolved last time.
            home_channel = existing.config.get("home_channel", "") if existing is not None else ""
        config["allowed_users"] = [owner_user_id]
        if home_channel:
            config["home_channel"] = home_channel
        metadata = {**metadata, "owner_user_id": owner_user_id, "owner_name": owner_name}

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


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
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
        return provider_common.absent()
    app_token = credential.credentials.get("app_token", "")
    bot_token = credential.credentials.get("bot_token", "")
    if not app_token or not bot_token:
        return provider_common.absent()
    return provider_common.has_token(
        secrets={"app_token": app_token, "bot_token": bot_token},
        expires_in=SLACK_BROKER_CACHE_SECONDS,
        config=credential.config,
        metadata=credential.metadata,
    )
