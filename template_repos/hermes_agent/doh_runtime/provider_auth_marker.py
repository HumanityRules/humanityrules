"""Add/remove local auth markers for DOH-managed model providers.

The real provider credentials live in DOH and are injected by the broker. The
local marker only tells upstream Hermes/WebUI that a provider is available so
it can render model choices and emit requests with DOH's placeholder bearer.

Run as the gateway user so auth.json/auth.lock stay owned by the sandbox user:

    python provider_auth_marker.py connect openai-codex
    python provider_auth_marker.py disconnect nous
"""

import datetime as dt
import sys

SENTINEL = "DOH_PLACEHOLDER"
CODEX_PROVIDER = "openai-codex"
NOUS_PROVIDER = "nous"
NOUS_PORTAL_BASE_URL = "https://portal.nousresearch.com"
NOUS_INFERENCE_BASE_URL = "https://inference-api.nousresearch.com/v1"
NOUS_OAUTH_CLIENT_ID = "hermes-cli"
NOUS_SCOPE = "inference:invoke inference:mint_agent_key"
NOUS_MARKER_EXPIRES_AT = "2999-01-01T00:00:00+00:00"
SUPPORTED_PROVIDERS = frozenset({CODEX_PROVIDER, NOUS_PROVIDER})


def _connect_codex(auth: object) -> None:
    """Write the Codex placeholder block unless one is already present."""
    try:
        existing = auth._read_codex_tokens()
        tokens = existing.get("tokens", {}) if isinstance(existing, dict) else {}
        if tokens.get("access_token") and tokens.get("refresh_token"):
            print("[provider-marker] openai-codex block already present; nothing to do")
            return
    except Exception:
        pass
    auth._save_codex_tokens({"access_token": SENTINEL, "refresh_token": SENTINEL})
    print(f"[provider-marker] wrote openai-codex placeholder block to {auth.get_hermes_home() / 'auth.json'}")


def _connect_nous(auth: object) -> None:
    """Write the Nous placeholder state using upstream's provider-state helper."""
    try:
        existing = auth.get_provider_auth_state(NOUS_PROVIDER)
        if isinstance(existing, dict) and existing.get("refresh_token") and existing.get("agent_key"):
            print("[provider-marker] nous block already present; nothing to do")
            return
    except Exception:
        pass

    now = dt.datetime.now(tz=dt.timezone.utc).isoformat()
    state = {
        "portal_base_url": NOUS_PORTAL_BASE_URL,
        "inference_base_url": NOUS_INFERENCE_BASE_URL,
        "client_id": NOUS_OAUTH_CLIENT_ID,
        "scope": NOUS_SCOPE,
        "token_type": "Bearer",
        "access_token": SENTINEL,
        "refresh_token": SENTINEL,
        "obtained_at": now,
        "expires_at": NOUS_MARKER_EXPIRES_AT,
        "expires_in": 999999999,
        "tls": {"insecure": False, "ca_bundle": None},
        "agent_key": SENTINEL,
        "agent_key_id": None,
        "agent_key_expires_at": NOUS_MARKER_EXPIRES_AT,
        "agent_key_expires_in": 999999999,
        "agent_key_reused": False,
        "agent_key_obtained_at": now,
    }
    auth.persist_nous_credentials(creds=state, label="DevOps Hero")
    if hasattr(auth, "invalidate_nous_auth_status_cache"):
        auth.invalidate_nous_auth_status_cache()
    print(f"[provider-marker] wrote nous placeholder block to {auth.get_hermes_home() / 'auth.json'}")


def _connect(auth: object, provider: str) -> None:
    """Write the provider's local placeholder marker."""
    if provider == CODEX_PROVIDER:
        _connect_codex(auth=auth)
        return
    if provider == NOUS_PROVIDER:
        _connect_nous(auth=auth)
        return
    raise ValueError(f"unsupported provider: {provider}")


def _disconnect(auth: object, provider: str) -> None:
    """Clear the provider's local auth marker."""
    cleared = auth.clear_provider_auth(provider)
    print(f"[provider-marker] cleared {provider} block: {cleared}")


def main(argv: list[str]) -> int:
    """Run the marker CLI."""
    if len(argv) != 3 or argv[1] not in ("connect", "disconnect") or argv[2] not in SUPPORTED_PROVIDERS:
        print("usage: provider_auth_marker.py {connect|disconnect} {openai-codex|nous}", file=sys.stderr)
        return 2
    action = argv[1]
    provider = argv[2]

    try:
        from hermes_cli import auth
    except Exception as exc:  # pragma: no cover - environment-specific import wiring
        print(f"[provider-marker] cannot import hermes_cli.auth: {exc}", file=sys.stderr)
        return 1

    try:
        if action == "connect":
            _connect(auth=auth, provider=provider)
        else:
            _disconnect(auth=auth, provider=provider)
    except Exception as exc:
        print(f"[provider-marker] {action} {provider} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv))
