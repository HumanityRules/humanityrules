"""OpenAI vault provider: API-key validation, persistence, and refresh outcome."""

import logging

import httpx
from django.utils import timezone

from humanityrules_app.models import App, Environment, IntegrationUserCredential, User
from humanityrules_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
OPENAI_VALIDATION_TIMEOUT_SECONDS = 20
OPENAI_BROKER_CACHE_SECONDS = 60 * 60
OPENAI_INVALID_KEY_MESSAGE = "OpenAI rejected this API key. Check that you pasted the complete key."


def schema(existing: IntegrationUserCredential | None, app: App | None, owner_user: User | None) -> dict:
    """Build the generic paste-form schema for OpenAI (`app`/`owner_user` unused)."""
    secret_configured = False
    metadata = {}
    if existing is not None:
        secret_configured = bool(existing.credentials.get("api_key"))
        metadata = existing.metadata
    return {
        "provider": "openai-api",
        "label": "OpenAI API Key",
        "status": "connected" if existing is not None else "not_connected",
        "secret_configured": secret_configured,
        "metadata": metadata,
        "message": "API keys are sent directly to the Humanity Rules vault. Your Hermes agent does not receive or store them.",
        "restart_required_after_save": True,
        "fields": [
            {
                "name": "api_key",
                "label": "API key",
                "kind": "secret",
                "required": existing is None,
                "placeholder": "sk-...",
                "help": "Paste an OpenAI API key. Leave blank to keep the current key.",
            },
        ],
    }


def _openai_validate_key(api_key: str) -> tuple[bool, str | None]:
    """Validate an OpenAI API key by listing models; return (ok, error)."""
    try:
        response = httpx.get(
            OPENAI_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=OPENAI_VALIDATION_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("openai validation request failed: %s", exc.__class__.__name__)
        return False, "OpenAI validation failed. Please try again."
    if response.status_code == 401:
        return False, OPENAI_INVALID_KEY_MESSAGE
    if response.status_code != 200:
        logger.error("openai key validation failed: status=%d", response.status_code)
        return False, "OpenAI rejected this API key."
    return True, None


def save_credentials(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    credentials_payload: dict,
    config_payload: dict,
) -> tuple[IntegrationUserCredential | None, str | None]:
    """Validate and persist an OpenAI API key for one logical app."""
    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.OPENAI,
    ).first()
    submitted_key = str(credentials_payload.get("api_key", "") or "").strip()
    existing_key = existing.credentials.get("api_key", "") if existing is not None else ""
    api_key = submitted_key or existing_key
    if not api_key:
        return None, "api_key is required"

    metadata = existing.metadata if existing is not None else {}
    if submitted_key:
        ok, openai_error = _openai_validate_key(api_key=api_key)
        if not ok:
            return None, openai_error
        metadata = {"validated_at": timezone.now().isoformat()}

    credential, _ = IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.OPENAI,
        defaults={
            "credentials": {"api_key": api_key},
            "config": {},
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    return credential, None


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for OpenAI (plain DB read)."""
    credential = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.OPENAI,
    ).first()
    if credential is None:
        return provider_common.absent()
    api_key = credential.credentials.get("api_key", "")
    if not api_key:
        return provider_common.absent()
    return provider_common.has_token(
        secrets={"api_key": api_key},
        expires_in=OPENAI_BROKER_CACHE_SECONDS,
        config=credential.config,
        metadata=credential.metadata,
    )
