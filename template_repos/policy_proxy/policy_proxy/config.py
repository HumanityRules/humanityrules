"""Policy-proxy configuration, loaded from environment variables at process start.

The sidecar verifies session JWTs via the central JWKS, calls the PDP, and
proxies authorized traffic to a local upstream container.

HUMR_APP_BEARER is the app's own control-plane credential: presenting it is what
tells HUMR which app is asking, so nothing this process sends has to name the app.
HUMR_APP_ID stays for logs and decision-cache keys only.

Required env: HUMR_APP_ID, HUMR_ENV_SLUG, HUMR_ENV_DOMAIN, HUMR_AUTH_BASE_URL,
HUMR_CONTROL_PLANE_URL, HUMR_JWKS_URL, HUMR_PDP_URL, HUMR_APP_BEARER,
HUMR_UPSTREAM_HOST, HUMR_UPSTREAM_PORT, HUMR_LISTEN_PORT.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyProxyConfig:
    app_id: str
    env_slug: str
    env_domain: str
    auth_base_url: str
    control_plane_url: str
    jwks_url: str
    pdp_url: str
    app_bearer_token: str
    upstream_host: str
    upstream_port: int
    listen_port: int
    # The agent's public hostname (<subdomain>.<env-domain>). Hosts of the
    # form <webapp-slug>-<public_hostname> are webapp hostnames and get the
    # anonymous public-grant check before the session flow. None when the
    # deployment provides no public hostname (no shared-ALB hosted zone, e.g.
    # local runs); then no Host is ever treated as a webapp hostname.
    public_hostname: str | None
    # Cache ttl for identity-keyed PDP allow/deny decisions, seconds. 0 disables caching.
    pdp_cache_ttl_seconds: int
    # Cache ttl for anonymous public-webapp decisions, seconds. Bounds both
    # grant latency (stale deny) and revocation latency (stale allow).
    public_cache_ttl_seconds: int
    # Authorized traffic reports are coalesced into one request per interval.
    activity_report_interval_seconds: int


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
        app_id=_required("HUMR_APP_ID"),
        env_slug=_required("HUMR_ENV_SLUG"),
        env_domain=_required("HUMR_ENV_DOMAIN"),
        auth_base_url=_required("HUMR_AUTH_BASE_URL").rstrip("/"),
        control_plane_url=_required("HUMR_CONTROL_PLANE_URL").rstrip("/"),
        jwks_url=_required("HUMR_JWKS_URL"),
        pdp_url=_required("HUMR_PDP_URL"),
        app_bearer_token=_required("HUMR_APP_BEARER"),
        upstream_host=_required("HUMR_UPSTREAM_HOST"),
        upstream_port=int(_required("HUMR_UPSTREAM_PORT")),
        listen_port=int(_required("HUMR_LISTEN_PORT")),
        public_hostname=os.environ.get("HUMR_PUBLIC_HOSTNAME") or None,
        pdp_cache_ttl_seconds=_int_env(name="HUMR_PDP_CACHE_TTL_SECONDS", default=60),
        public_cache_ttl_seconds=_int_env(name="HUMR_PUBLIC_CACHE_TTL_SECONDS", default=10),
        activity_report_interval_seconds=_int_env(
            name="HUMR_POLICY_PROXY_ACTIVITY_INTERVAL_SECONDS",
            default=300,
        ),
    )
