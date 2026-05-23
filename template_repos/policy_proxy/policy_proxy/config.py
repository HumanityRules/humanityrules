"""Policy-proxy configuration, loaded from environment variables at process start.

The sidecar verifies session JWTs via the central JWKS, calls the PDP, and
proxies authorized traffic to a local upstream container.

Required env: DOH_APP_ID, DOH_ENV_SLUG, DOH_ENV_DOMAIN, DOH_AUTH_BASE_URL,
DOH_JWKS_URL, DOH_PDP_URL, DOH_ENV_BEARER, DOH_UPSTREAM_HOST, DOH_UPSTREAM_PORT,
DOH_LISTEN_PORT.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyProxyConfig:
    app_id: str
    env_slug: str
    env_domain: str
    auth_base_url: str
    jwks_url: str
    pdp_url: str
    env_bearer_token: str
    upstream_host: str
    upstream_port: int
    listen_port: int
    # Cache ttl for PDP allow/deny decisions, seconds. 0 disables caching.
    pdp_cache_ttl_seconds: int


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"required env var {name!r} is not set")
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def load_proxy_config_from_env() -> PolicyProxyConfig:
    """Load sidecar config from the process environment."""
    return PolicyProxyConfig(
        app_id=_required("DOH_APP_ID"),
        env_slug=_required("DOH_ENV_SLUG"),
        env_domain=_required("DOH_ENV_DOMAIN"),
        auth_base_url=_required("DOH_AUTH_BASE_URL").rstrip("/"),
        jwks_url=_required("DOH_JWKS_URL"),
        pdp_url=_required("DOH_PDP_URL"),
        env_bearer_token=_required("DOH_ENV_BEARER"),
        upstream_host=_required("DOH_UPSTREAM_HOST"),
        upstream_port=int(_required("DOH_UPSTREAM_PORT")),
        listen_port=int(_required("DOH_LISTEN_PORT")),
        pdp_cache_ttl_seconds=_int_env(name="DOH_PDP_CACHE_TTL_SECONDS", default=600),
    )
