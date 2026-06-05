"""Single source of truth for the per-user integration providers.

Every provider lives in a `provider_<slug>.py` module exposing a uniform
interface. This registry maps each provider slug to a `ProviderSpec` so the
integration endpoints (`token_refresh_batch`, `user_credential_vault`,
`provider_disconnect`) dispatch through one table instead of each keeping
its own per-provider dict. Adding a provider is one row here plus its module.

Two `ProviderKind`s, differing only in how credentials are obtained and torn
down:

- OAUTH (`provider_google`, `provider_github`, `provider_openai_codex`,
  `provider_nous`): connect via a browser redirect dance or broker-run device
  flow, refresh by upstream token exchange, disconnect deletes the row +
  best-effort upstream `revoke`.
- VAULT (`provider_openrouter`, `provider_slack`, `provider_telegram`):
  connect via a browser-direct credential paste (`schema` + `save_credentials`),
  refresh is a DB read, disconnect just deletes the row.

Uniform module interface by kind:
- all providers: `refresh_outcome(environment, owner_user, app_slug) -> dict`
- VAULT only: `schema(existing) -> dict`,
  `save_credentials(owner_user, environment, app_slug, credentials_payload,
  config_payload) -> (IntegrationUserCredential | None, error | None)`
- OAUTH only: `revoke(refresh_token) -> None`
- device-flow OAUTH only: `store_device_credentials(environment, owner_user,
  app_slug, payload) -> (status, body)`

The spec stores the *module*, not bound functions, so endpoints resolve
`spec.module.<fn>` at call time. That late binding is deliberate: it keeps
`unittest.mock.patch("...provider_<slug>.<fn>")` seams working.
"""

import dataclasses
import enum
from types import ModuleType

from devopshero_app.models import IntegrationUserCredential
from devopshero_app.views.integrations import (
    provider_github,
    provider_google,
    provider_nous,
    provider_openai_codex,
    provider_openrouter,
    provider_slack,
    provider_telegram,
)


class ProviderKind(enum.Enum):
    """How a provider's credentials are obtained and disconnected."""

    OAUTH = "oauth"
    VAULT = "vault"


@dataclasses.dataclass(frozen=True)
class ProviderSpec:
    """Everything the integration endpoints need to dispatch one provider."""

    provider: str
    kind: ProviderKind
    module: ModuleType


_SPECS = [
    ProviderSpec(provider=IntegrationUserCredential.Provider.GOOGLE, kind=ProviderKind.OAUTH, module=provider_google),
    ProviderSpec(provider=IntegrationUserCredential.Provider.GITHUB, kind=ProviderKind.OAUTH, module=provider_github),
    ProviderSpec(provider=IntegrationUserCredential.Provider.SLACK, kind=ProviderKind.VAULT, module=provider_slack),
    ProviderSpec(provider=IntegrationUserCredential.Provider.TELEGRAM, kind=ProviderKind.VAULT, module=provider_telegram),
    ProviderSpec(provider=IntegrationUserCredential.Provider.OPENAI_CODEX, kind=ProviderKind.OAUTH, module=provider_openai_codex),
    ProviderSpec(provider=IntegrationUserCredential.Provider.OPENROUTER, kind=ProviderKind.VAULT, module=provider_openrouter),
    ProviderSpec(provider=IntegrationUserCredential.Provider.NOUS, kind=ProviderKind.OAUTH, module=provider_nous),
]

# Keyed by the provider slug. The keys are `Provider` enum members, which are
# `str` subclasses, so bare-string lookups (e.g. a slug from a JSON body) and
# enum lookups both resolve to the same entry.
REGISTRY: dict[str, ProviderSpec] = {spec.provider: spec for spec in _SPECS}


def get(provider: str) -> ProviderSpec | None:
    """Return the spec for a provider slug, or None if the slug is unknown."""
    return REGISTRY.get(provider)


def get_of_kind(provider: str, kind: ProviderKind) -> ProviderSpec | None:
    """Return the spec for a provider slug only if it is of *kind*, else None."""
    spec = REGISTRY.get(provider)
    if spec is None or spec.kind != kind:
        return None
    return spec
