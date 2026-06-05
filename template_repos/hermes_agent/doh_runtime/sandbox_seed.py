"""Seed sandbox-local runtime files before process-compose starts."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

import provider_auth_marker

AWS_STS_PORT = 9901
AWS_BEDROCK_PORT = 9902
AWS_BEDROCK_RUNTIME_PORT = 9903
AWS_CE_PORT = 9904


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
    if provider not in provider_auth_marker.SUPPORTED_PROVIDERS:
        return 0

    # Credential model A: the real model-provider token lives outside the
    # sandbox and the broker swaps it onto the wire, but Hermes/WebUI still need
    # local provider state to render model choices and emit placeholder-backed
    # requests. Seed only the provider block via Hermes' own locked/atomic writer.
    try:
        from hermes_cli import auth
    except Exception as exc:  # pragma: no cover - import wiring is environment-specific
        print(f"[sandbox-seed] cannot import hermes_cli.auth for {provider}: {exc}", file=sys.stderr)
        return 1

    try:
        provider_auth_marker._connect(auth=auth, provider=provider)
    except Exception as exc:
        print(f"[sandbox-seed] failed to seed {provider} placeholder: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Run all sandbox-local boot seeds."""
    hermes_home = _hermes_home()
    workspace = Path(_required_env(name="HERMES_WEBUI_DEFAULT_WORKSPACE")).expanduser()
    aws_region = _required_env(name="AWS_DEFAULT_REGION")

    write_child_aws_config(workspace=workspace, aws_region=aws_region)
    seed_soul_file(hermes_home=hermes_home)
    configure_github_git_helper()
    return seed_provider_placeholder(hermes_home=hermes_home)


if __name__ == "__main__":
    sys.exit(main())
