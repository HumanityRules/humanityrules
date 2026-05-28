"""Configuration for the local policy-proxy shim (compose dev only)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalPolicyProxyConfig:
    upstream_host: str
    upstream_port: int
    listen_port: int


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"required env var {name!r} is not set")
    return value


def load_config_from_env() -> LocalPolicyProxyConfig:
    """Load config from the process environment."""
    return LocalPolicyProxyConfig(
        upstream_host=_required("DOH_UPSTREAM_HOST"),
        upstream_port=int(_required("DOH_UPSTREAM_PORT")),
        listen_port=int(_required("DOH_LISTEN_PORT")),
    )
