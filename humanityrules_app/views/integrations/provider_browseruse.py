"""Browser Use vault provider: API-key persistence and refresh outcome.

Browser Use authenticates with the `X-Browser-Use-API-Key` header (not
`Authorization: Bearer`). That only matters on the Hermes-side proxy, which
injects the key on the wire; here on the control plane we just store it.

Unlike OpenAI/Anthropic, we do NOT validate the key upstream on save: Browser
Use exposes no documented lightweight, side-effect-free GET that the agent
already relies on, and its session-create endpoint (`POST /api/v3/browsers`)
provisions a billable browser. The key is validated implicitly on first use.
"""

import logging

from humanityrules_app.models import App, Environment, IntegrationUserCredential, User
from humanityrules_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


BROWSERUSE_BROKER_CACHE_SECONDS = 60 * 60


def schema(existing: IntegrationUserCredential | None, app: App | None, owner_user: User | None) -> dict:
    """Build the generic paste-form schema for Browser Use (`app`/`owner_user` unused)."""
    secret_configured = False
    metadata = {}
    if existing is not None:
        secret_configured = bool(existing.credentials.get("api_key"))
        metadata = existing.metadata
    return {
        "provider": "browseruse",
        "label": "Browser Use",
        "status": "connected" if existing is not None else "not_connected",
        "secret_configured": secret_configured,
        "metadata": metadata,
        "message": "API keys are sent directly to the DevOps Hero vault. Your Hermes agent does not receive or store them.",
        "restart_required_after_save": True,
        "fields": [
            {
                "name": "api_key",
                "label": "API key",
                "kind": "secret",
                "required": existing is None,
                "placeholder": "bu_...",
                "help": "Paste a Browser Use API key from browser-use.com. Leave blank to keep the current key.",
            },
        ],
    }


def save_credentials(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    credentials_payload: dict,
    config_payload: dict,
) -> tuple[IntegrationUserCredential | None, str | None]:
    """Persist a Browser Use API key for one logical app (store-only, no upstream validation)."""
    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.BROWSERUSE,
    ).first()
    submitted_key = str(credentials_payload.get("api_key", "") or "").strip()
    existing_key = existing.credentials.get("api_key", "") if existing is not None else ""
    api_key = submitted_key or existing_key
    if not api_key:
        return None, "api_key is required"

    metadata = existing.metadata if existing is not None else {}

    credential, _ = IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.BROWSERUSE,
        defaults={
            "credentials": {"api_key": api_key},
            "config": {},
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    return credential, None


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for Browser Use (plain DB read)."""
    credential = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.BROWSERUSE,
    ).first()
    if credential is None:
        return provider_common.absent()
    api_key = credential.credentials.get("api_key", "")
    if not api_key:
        return provider_common.absent()
    return provider_common.has_token(
        secrets={"api_key": api_key},
        expires_in=BROWSERUSE_BROKER_CACHE_SECONDS,
        config=credential.config,
        metadata=credential.metadata,
    )
