"""Policy-proxy configuration, loaded from environment variables at process start.

The same binary runs in two roles. Selected by `DOH_ROLE`:

- `proxy`: the per-app sidecar that verifies JWTs, calls the PDP, and proxies
  to a local upstream. Required env: DOH_APP_ID, DOH_JWKS_URL, DOH_PDP_URL,
  DOH_POLICY_PROXY_TOKEN, DOH_UPSTREAM_HOST, DOH_UPSTREAM_PORT.
- `auth`: the singleton per-env service that runs the Okta OAuth dance and
  mints session JWTs. Required env: DOH_POLICY_PROXY_AUTH_CONFIG_SECRET_ARN.
  Optional: DOH_SESSION_TTL_SECONDS.

Shared required env (both roles): DOH_ENV_DOMAIN, DOH_AUTH_BASE_URL, DOH_LISTEN_PORT.
"""

import os
from dataclasses import dataclass


ROLE_PROXY = "proxy"
ROLE_AUTH = "auth"


@dataclass(frozen=True)
class PolicyProxyConfig:
    """Sidecar-role configuration. Populated only when DOH_ROLE=proxy."""
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


@dataclass(frozen=True)
class AuthServiceConfig:
    """Auth-service-role configuration. Populated only when DOH_ROLE=auth."""
    env_domain: str
    auth_base_url: str
    listen_port: int
    # ARN of the Secrets Manager secret holding {oidc_config, jwt_key}.
    auth_config_secret_arn: str
    session_ttl_seconds: int


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


def role_from_env() -> str:
    """Return the DOH_ROLE env var, defaulting to 'proxy' for backwards compat."""
    raw = os.environ.get("DOH_ROLE", ROLE_PROXY).strip().lower()
    if raw not in (ROLE_PROXY, ROLE_AUTH):
        raise RuntimeError(f"DOH_ROLE must be one of {{proxy, auth}}, got {raw!r}")
    return raw


def load_proxy_config_from_env() -> PolicyProxyConfig:
    """Load sidecar-role config from the process environment."""
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
        pdp_cache_ttl_seconds=_int_env(name="DOH_PDP_CACHE_TTL_SECONDS", default=600),
    )


# 30 days — matches the historical auth-Lambda default.
DEFAULT_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


def load_auth_config_from_env() -> AuthServiceConfig:
    """Load auth-service-role config from the process environment."""
    return AuthServiceConfig(
        env_domain=_required("DOH_ENV_DOMAIN"),
        auth_base_url=_required("DOH_AUTH_BASE_URL").rstrip("/"),
        listen_port=int(_required("DOH_LISTEN_PORT")),
        auth_config_secret_arn=_required("DOH_POLICY_PROXY_AUTH_CONFIG_SECRET_ARN"),
        session_ttl_seconds=_int_env(name="DOH_SESSION_TTL_SECONDS", default=DEFAULT_SESSION_TTL_SECONDS),
    )
