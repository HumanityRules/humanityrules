"""Anthropic vault provider: API-key validation, persistence, and refresh outcome.

Anthropic authenticates with the `x-api-key` header (plus a required
`anthropic-version`), not `Authorization: Bearer`. That only matters on the
Hermes-side proxy, which injects the key on the wire; here on the control plane
we just validate the key and store it, exactly like OpenRouter/OpenAI.
"""

import logging

import httpx
from django.utils import timezone

from devopshero_app.models import App, Environment, IntegrationUserCredential, User
from devopshero_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_VALIDATION_TIMEOUT_SECONDS = 20
ANTHROPIC_BROKER_CACHE_SECONDS = 60 * 60
ANTHROPIC_INVALID_KEY_MESSAGE = "Anthropic rejected this API key. Check that you pasted the complete key."


def schema(existing: IntegrationUserCredential | None, app: App | None, owner_user: User | None) -> dict:
    """Build the generic paste-form schema for Anthropic (`app`/`owner_user` unused)."""
    secret_configured = False
    metadata = {}
    if existing is not None:
        secret_configured = bool(existing.credentials.get("api_key"))
        metadata = existing.metadata
    return {
        "provider": "anthropic",
        "label": "Anthropic",
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
                "placeholder": "sk-ant-...",
                "help": "Paste an Anthropic API key. Leave blank to keep the current key.",
            },
        ],
    }


def _anthropic_validate_key(api_key: str) -> tuple[bool, str | None]:
    """Validate an Anthropic API key by listing models; return (ok, error)."""
    try:
        response = httpx.get(
            ANTHROPIC_MODELS_URL,
            headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_API_VERSION},
            timeout=ANTHROPIC_VALIDATION_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("anthropic validation request failed: %s", exc.__class__.__name__)
        return False, "Anthropic validation failed. Please try again."
    if response.status_code == 401:
        return False, ANTHROPIC_INVALID_KEY_MESSAGE
    if response.status_code != 200:
        logger.error("anthropic key validation failed: status=%d", response.status_code)
        return False, "Anthropic rejected this API key."
    return True, None


def save_credentials(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    credentials_payload: dict,
    config_payload: dict,
) -> tuple[IntegrationUserCredential | None, str | None]:
    """Validate and persist an Anthropic API key for one logical app."""
    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.ANTHROPIC,
    ).first()
    submitted_key = str(credentials_payload.get("api_key", "") or "").strip()
    existing_key = existing.credentials.get("api_key", "") if existing is not None else ""
    api_key = submitted_key or existing_key
    if not api_key:
        return None, "api_key is required"

    metadata = existing.metadata if existing is not None else {}
    if submitted_key:
        ok, anthropic_error = _anthropic_validate_key(api_key=api_key)
        if not ok:
            return None, anthropic_error
        metadata = {"validated_at": timezone.now().isoformat()}

    credential, _ = IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.ANTHROPIC,
        defaults={
            "credentials": {"api_key": api_key},
            "config": {},
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    return credential, None


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for Anthropic (plain DB read)."""
    credential = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.ANTHROPIC,
    ).first()
    if credential is None:
        return provider_common.absent()
    api_key = credential.credentials.get("api_key", "")
    if not api_key:
        return provider_common.absent()
    return provider_common.has_token(
        secrets={"api_key": api_key},
        expires_in=ANTHROPIC_BROKER_CACHE_SECONDS,
        config=credential.config,
        metadata=credential.metadata,
    )
