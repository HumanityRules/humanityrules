"""Seed sandbox-local runtime files before process-compose starts.

Also exposes ``--auth-marker connect|disconnect <provider>`` for the root broker
to add/remove local placeholder blocks in auth.json as the gateway user.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

AWS_STS_PORT = 9901
AWS_BEDROCK_PORT = 9902
AWS_BEDROCK_RUNTIME_PORT = 9903
AWS_CE_PORT = 9904
AWS_S3_PORT = 9905
AWS_S3TABLES_PORT = 9906

AUTH_MARKER_SENTINEL = "DOH_PLACEHOLDER"
CODEX_PROVIDER = "openai-codex"
NOUS_PROVIDER = "nous"
NOUS_PORTAL_BASE_URL = "https://portal.nousresearch.com"
NOUS_INFERENCE_BASE_URL = "https://inference-api.nousresearch.com/v1"
NOUS_OAUTH_CLIENT_ID = "hermes-cli"
NOUS_SCOPE = "inference:invoke inference:mint_agent_key"
NOUS_MARKER_EXPIRES_AT = "2999-01-01T00:00:00+00:00"
SUPPORTED_AUTH_MARKER_PROVIDERS = frozenset({CODEX_PROVIDER, NOUS_PROVIDER})


def _required_env(name: str) -> str:
    """Read a required environment variable."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def _hermes_home() -> Path:
    """Resolve Hermes home without importing the agent package."""
    return Path(_required_env(name="HERMES_HOME")).expanduser()


def write_child_aws_config(workspace: Path, aws_region: str) -> None:
    """Write in-sandbox AWS SDK config that targets the local signer."""
    aws_dir = workspace / ".aws"
    aws_dir.mkdir(parents=True, exist_ok=True)
    (aws_dir / "config").write_text(
        "\n".join(
            [
                "[default]",
                f"region = {aws_region}",
                "services = hermes-nono-endpoints",
                # S3 must be path-style: a virtual-host bucket prefix
                # (bucket.127.0.0.1) can't reach the loopback signer. botocore
                # already forces path-style for IP endpoints; pin it so a future
                # default change can't silently break the proxy.
                "s3 =",
                "  addressing_style = path",
                "",
                "[services hermes-nono-endpoints]",
                "sts =",
                f"  endpoint_url = http://127.0.0.1:{AWS_STS_PORT}",
                "",
                "bedrock =",
                f"  endpoint_url = http://127.0.0.1:{AWS_BEDROCK_PORT}",
                "",
                "bedrock_runtime =",
                f"  endpoint_url = http://127.0.0.1:{AWS_BEDROCK_RUNTIME_PORT}",
                "",
                "cost_explorer =",
                f"  endpoint_url = http://127.0.0.1:{AWS_CE_PORT}",
                "",
                "s3 =",
                f"  endpoint_url = http://127.0.0.1:{AWS_S3_PORT}",
                "",
                "s3tables =",
                f"  endpoint_url = http://127.0.0.1:{AWS_S3TABLES_PORT}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (aws_dir / "credentials").write_text(
        "\n".join(
            [
                "[default]",
                "aws_access_key_id = dummy",
                "aws_secret_access_key = dummy",
                "",
            ]
        ),
        encoding="utf-8",
    )


def seed_soul_file(hermes_home: Path) -> None:
    """Seed SOUL.md once from the image-owned default."""
    source = Path("/opt/hermes/SOUL.md")
    target = hermes_home / "SOUL.md"
    if source.is_file() and not target.exists():
        shutil.copy2(src=source, dst=target)


def configure_github_git_helper() -> None:
    """Configure git to send the GitHub placeholder credential through the broker."""
    subprocess.run(
        [
            "git",
            "config",
            "--global",
            "--replace-all",
            "credential.https://github.com.helper",
            "!f(){ echo username=x-access-token; echo password=DOH_PLACEHOLDER; }; f",
        ],
        check=True,
    )


def _connect_codex_auth_marker(auth: object) -> None:
    """Write the Codex placeholder block unless one is already present."""
    try:
        existing = auth._read_codex_tokens()
        tokens = existing.get("tokens", {}) if isinstance(existing, dict) else {}
        if tokens.get("access_token") and tokens.get("refresh_token"):
            print("[provider-marker] openai-codex block already present; nothing to do")
            return
    except Exception:
        pass
    auth._save_codex_tokens({"access_token": AUTH_MARKER_SENTINEL, "refresh_token": AUTH_MARKER_SENTINEL})
    print(f"[provider-marker] wrote openai-codex placeholder block to {auth.get_hermes_home() / 'auth.json'}")


def _build_nous_marker_jwt() -> str:
    """Build an unsigned placeholder JWT that passes Hermes' local invoke-JWT validation.

    Since Hermes Agent v2026.6.5, `resolve_nous_runtime_credentials` requires the
    access token to decode as a JWT carrying the `inference:invoke` scope and an
    unexpired `exp`. A bare sentinel string fails that check, which forces a token
    refresh against the real Nous portal (not TLS-intercepted) with the placeholder
    refresh token — the resulting invalid_grant quarantines the whole auth block and
    empties the WebUI model picker. Hermes only base64-decodes the payload (no
    signature verification), so an unsigned far-future JWT keeps the marker inert;
    the broker proxy still swaps the real token onto the wire.
    """
    header = {"alg": "none", "typ": "JWT"}
    claims = {
        "sub": AUTH_MARKER_SENTINEL,
        "scope": NOUS_SCOPE,
        "exp": int(dt.datetime.fromisoformat(NOUS_MARKER_EXPIRES_AT).timestamp()),
    }
    segments = [
        base64.urlsafe_b64encode(json.dumps(part).encode("utf-8")).decode("ascii").rstrip("=")
        for part in (header, claims)
    ]
    # The sentinel rides as the (never-verified) signature segment so the
    # marker stays grep-able in auth.json.
    return ".".join([*segments, AUTH_MARKER_SENTINEL])


def _is_jwt_shaped(token: object) -> bool:
    """Return whether a value has JWT structure (three dot-separated segments)."""
    return isinstance(token, str) and token.count(".") == 2


def _connect_nous_auth_marker(auth: object) -> None:
    """Write the Nous placeholder state using upstream's provider-state helper."""
    try:
        # A pre-JWT marker (bare sentinel) must be upgraded, not kept: Hermes
        # v2026.6.5+ quarantines it on first credential resolve.
        existing = auth.get_provider_auth_state(NOUS_PROVIDER)
        if isinstance(existing, dict) and existing.get("refresh_token") and _is_jwt_shaped(existing.get("agent_key")):
            print("[provider-marker] nous block already present; nothing to do")
            return
    except Exception:
        pass

    now = dt.datetime.now(tz=dt.timezone.utc).isoformat()
    marker_jwt = _build_nous_marker_jwt()
    state = {
        "portal_base_url": NOUS_PORTAL_BASE_URL,
        "inference_base_url": NOUS_INFERENCE_BASE_URL,
        "client_id": NOUS_OAUTH_CLIENT_ID,
        "scope": NOUS_SCOPE,
        "token_type": "Bearer",
        "access_token": marker_jwt,
        "refresh_token": AUTH_MARKER_SENTINEL,
        "obtained_at": now,
        "expires_at": NOUS_MARKER_EXPIRES_AT,
        "expires_in": 999999999,
        "tls": {"insecure": False, "ca_bundle": None},
        "agent_key": marker_jwt,
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


def _connect_auth_marker(auth: object, provider: str) -> None:
    """Write the provider's local placeholder marker."""
    if provider == CODEX_PROVIDER:
        _connect_codex_auth_marker(auth=auth)
        return
    if provider == NOUS_PROVIDER:
        _connect_nous_auth_marker(auth=auth)
        return
    raise ValueError(f"unsupported provider: {provider}")


def _disconnect_auth_marker(auth: object, provider: str) -> None:
    """Clear the provider's local auth marker."""
    cleared = auth.clear_provider_auth(provider)
    print(f"[provider-marker] cleared {provider} block: {cleared}")


def run_auth_marker(*, action: str, provider: str) -> int:
    """Add or remove a DOH-managed provider block in auth.json."""
    try:
        from hermes_cli import auth  # pyright: ignore[reportMissingImports]
    except Exception as exc:  # pragma: no cover - import wiring is environment-specific
        print(f"[provider-marker] cannot import hermes_cli.auth: {exc}", file=sys.stderr)
        return 1

    try:
        if action == "connect":
            _connect_auth_marker(auth=auth, provider=provider)
        else:
            _disconnect_auth_marker(auth=auth, provider=provider)
    except Exception as exc:
        print(f"[provider-marker] {action} {provider} failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _configured_model_provider(config_path: Path) -> str | None:
    """Return the rendered runtime config's model provider."""
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[sandbox-seed] config not found at {config_path}; skipping provider placeholder")
        return None
    except yaml.YAMLError as exc:
        print(f"[sandbox-seed] cannot parse {config_path}: {exc}; skipping provider placeholder", file=sys.stderr)
        return None

    if not isinstance(payload, dict):
        return None
    model = payload.get("model")
    if not isinstance(model, dict):
        return None
    provider = str(model.get("provider") or "").strip()
    return provider or None


def seed_provider_placeholder(hermes_home: Path) -> int:
    """Seed a placeholder auth marker when a DOH-managed provider is the default backend."""
    provider = _configured_model_provider(config_path=hermes_home / "config.yaml")
    if provider not in SUPPORTED_AUTH_MARKER_PROVIDERS:
        return 0

    # Credential model A: the real model-provider token lives outside the
    # sandbox and the broker swaps it onto the wire, but Hermes/WebUI still need
    # local provider state to render model choices and emit placeholder-backed
    # requests. Seed only the provider block via Hermes' own locked/atomic writer.
    try:
        from hermes_cli import auth  # pyright: ignore[reportMissingImports]
    except Exception as exc:  # pragma: no cover - import wiring is environment-specific
        print(f"[sandbox-seed] cannot import hermes_cli.auth for {provider}: {exc}", file=sys.stderr)
        return 1

    try:
        _connect_auth_marker(auth=auth, provider=provider)
    except Exception as exc:
        print(f"[sandbox-seed] failed to seed {provider} placeholder: {exc}", file=sys.stderr)
        return 1
    return 0


def run_boot_seed() -> int:
    """Run all sandbox-local boot seeds."""
    hermes_home = _hermes_home()
    workspace = Path(_required_env(name="HERMES_WEBUI_DEFAULT_WORKSPACE")).expanduser()
    aws_region = _required_env(name="AWS_DEFAULT_REGION")

    write_child_aws_config(workspace=workspace, aws_region=aws_region)
    seed_soul_file(hermes_home=hermes_home)
    configure_github_git_helper()
    return seed_provider_placeholder(hermes_home=hermes_home)


def _dispatch(argv: list[str]) -> int:
    """Run boot seed or a broker-invoked auth-marker update."""
    if len(argv) >= 3 and argv[0] == "--auth-marker":
        action = argv[1]
        provider = argv[2]
        if action not in ("connect", "disconnect") or provider not in SUPPORTED_AUTH_MARKER_PROVIDERS:
            print(
                "usage: sandbox_seed.py --auth-marker {connect|disconnect} {openai-codex|nous}",
                file=sys.stderr,
            )
            return 2
        return run_auth_marker(action=action, provider=provider)
    if argv:
        print(
            "usage: sandbox_seed.py [--auth-marker {connect|disconnect} {openai-codex|nous}]",
            file=sys.stderr,
        )
        return 2
    return run_boot_seed()


if __name__ == "__main__":
    sys.exit(_dispatch(argv=sys.argv[1:]))
