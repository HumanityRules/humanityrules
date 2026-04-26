"""Policy-proxy configuration, loaded from environment variables at process start."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyProxyConfig:
    """All the env-var-driven knobs the policy proxy reads once at startup."""
    app_id: str
    env_slug: str
    env_domain: str
    auth_base_url: str
    jwks_url: str
    pdp_url: str
    policy_proxy_token: str
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


def load_config_from_env() -> PolicyProxyConfig:
    """Load and validate all policy-proxy configuration from the process environment."""
    return PolicyProxyConfig(
        app_id=_required("DOH_APP_ID"),
        env_slug=_required("DOH_ENV_SLUG"),
        env_domain=_required("DOH_ENV_DOMAIN"),
        auth_base_url=_required("DOH_AUTH_BASE_URL").rstrip("/"),
        jwks_url=_required("DOH_JWKS_URL"),
        pdp_url=_required("DOH_PDP_URL"),
        policy_proxy_token=_required("DOH_POLICY_PROXY_TOKEN"),
        upstream_host=_required("DOH_UPSTREAM_HOST"),
        upstream_port=int(_required("DOH_UPSTREAM_PORT")),
        listen_port=int(_required("DOH_LISTEN_PORT")),
        pdp_cache_ttl_seconds=_int_env("DOH_PDP_CACHE_TTL_SECONDS", 600),
    )
