"""Shared building blocks for the per-user integration providers.

Two provider families live alongside this module, organized symmetrically:

- OAuth providers (`provider_google`, `provider_github`): connect via a
  browser redirect dance, refresh by exchanging a stored refresh_token
  upstream, disconnect by deleting the row + best-effort upstream revoke.
- Vault providers (`provider_openrouter`, `provider_slack`, `provider_telegram`):
  connect via a browser-direct credential paste, refresh by reading the row
  back, and carry their own non-secret `config`/`metadata`.

This module holds what both families (or both OAuth providers) would
otherwise duplicate: the redirect-flow request helpers, the refresh-exchange
result wrapper, and the broker-outcome constructors that single-source the
`{outcome, secrets?, expires_in?, config?, metadata?}` contract the batched
refresh endpoint returns. See docs/integrations_broker_design.md.
"""

import logging
from urllib.parse import urlencode, urlparse

from django.utils import timezone

from devopshero_app.models import App, Environment, ResourceTag, User

logger = logging.getLogger(__name__)


def resolve_env_by_rd(rd: str, user: User) -> Environment | None:
    """Return the Environment whose shared_alb_hosted_zone suffixes *rd*'s host, or None.

    Accepts any URL whose host is a subdomain of a known env's hosted zone —
    e.g. rd `https://hermes.dev.example.com/x` matches an Environment with
    `shared_alb_hosted_zone = "dev.example.com"`. Scoped to envs in orgs
    *user* is a member of.
    """
    if not rd:
        return None
    parsed = urlparse(rd)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    candidates = (
        Environment.objects
        .exclude(shared_alb_hosted_zone="")
        .filter(aws_account__organization__memberships__user=user)
        .only("id", "slug", "shared_alb_hosted_zone")
    )
    for env in candidates:
        zone = env.shared_alb_hosted_zone.lower()
        if host == zone or host.endswith("." + zone):
            return env
    return None


def resolve_owned_app_slug(app_slug: str, env: Environment, owner_username: str) -> str | None:
    """Return app_slug when it identifies an app owned by *owner_username* in env's org.

    Redirect-flow form: returns None so the caller can render an
    HttpResponseBadRequest. The vault API surface uses its own variant that
    returns a JsonResponse error tuple instead.
    """
    if not app_slug:
        return None
    app = App.objects.filter(
        organization=env.aws_account.organization,
        slug=app_slug,
    ).first()
    if app is None:
        return None
    owner_tag = ResourceTag.objects.filter(
        resource_type=ResourceTag.ResourceType.APP,
        app=app,
        key="owner",
        value=owner_username,
    ).first()
    return app.slug if owner_tag is not None else None


def append_query(url: str, extra: dict[str, str]) -> str:
    """Append *extra* query params to *url*, preserving any existing query string."""
    parsed = urlparse(url)
    encoded = urlencode(extra)
    combined = f"{parsed.query}&{encoded}" if parsed.query else encoded
    return parsed._replace(query=combined).geturl()


class ExchangeResult:
    """Outcome of an OAuth refresh-token exchange.

    Exactly one of `response`, `revoked`, or `error` is populated on any
    given instance. `revoked` means upstream rejected the refresh_token as
    permanently unusable (caller should delete the row); `error` is any
    transient/other failure (network, 5xx, non-JSON).
    """

    def __init__(self, response: dict | None, revoked: bool, error: str | None) -> None:
        self.response = response or {}
        self.revoked = revoked
        self.error = error


def now() -> timezone.datetime:
    """Indirection for tests to patch."""
    return timezone.now()


# --- Broker refresh-outcome contract -----------------------------------------
# The batched refresh endpoint (provider_registry-driven token_refresh_batch)
# returns one of these per provider slug. `outcome` is the discriminator:
# `has_token` carries the secrets to inject + the broker cache lifetime;
# `absent` means no connected row (a normal 200 entry, not an error);
# `transient` means refresh failed and the broker should keep serving its
# still-valid cached token.


def has_token(secrets: dict, expires_in: int, config: dict, metadata: dict) -> dict:
    """Build the broker `has_token` outcome.

    OAuth providers pass empty `config`/`metadata` (the access token is the
    whole payload); vault providers pass the row's stored values.
    """
    return {
        "outcome": "has_token",
        "secrets": secrets,
        "expires_in": expires_in,
        "config": config,
        "metadata": metadata,
    }


def absent() -> dict:
    """Build the broker `absent` outcome: no connected credential row."""
    return {"outcome": "absent"}


def transient() -> dict:
    """Build the broker `transient` outcome: refresh failed; serve the cached token."""
    return {"outcome": "transient"}
