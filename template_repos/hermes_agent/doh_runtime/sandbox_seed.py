"""Seed sandbox-local runtime files before process-compose starts."""

from __future__ import annotations

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


def _codex_model_provider_configured(config_path: Path) -> bool:
    """Return whether the rendered runtime config selects Codex as the default provider."""
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[sandbox-seed] config not found at {config_path}; skipping codex placeholder")
        return False
    except yaml.YAMLError as exc:
        print(f"[sandbox-seed] cannot parse {config_path}: {exc}; skipping codex placeholder", file=sys.stderr)
        return False

    if not isinstance(payload, dict):
        return False
    model = payload.get("model")
    if not isinstance(model, dict):
        return False
    return str(model.get("provider") or "").strip() == "openai-codex"


def seed_codex_placeholder(hermes_home: Path) -> int:
    """Seed a placeholder Codex token when Codex is the default backend."""
    if not _codex_model_provider_configured(config_path=hermes_home / "config.yaml"):
        return 0

    # Credential model A: the real ChatGPT access token lives outside the
    # sandbox and the broker swaps it onto the wire, but Hermes will not emit a
    # Codex request unless auth.json already has both access_token and
    # refresh_token for openai-codex. Seed only that singleton provider block via
    # Hermes' own locked/atomic writer.
    try:
        from hermes_cli import auth
    except Exception as exc:  # pragma: no cover - import wiring is environment-specific
        print(f"[sandbox-seed] cannot import hermes_cli.auth: {exc}", file=sys.stderr)
        return 1

    try:
        existing = auth._read_codex_tokens()
        tokens = existing.get("tokens", {}) if isinstance(existing, dict) else {}
        if tokens.get("access_token") and tokens.get("refresh_token"):
            print("[sandbox-seed] codex token already present; leaving it untouched")
            return 0
    except Exception:
        pass

    # Non-JWT strings have no parseable exp claim, so Hermes treats the sentinel
    # as non-expiring and does not try to self-refresh or rewrite auth.json.
    auth._save_codex_tokens({"access_token": "DOH_PLACEHOLDER", "refresh_token": "DOH_PLACEHOLDER"})
    print(f"[sandbox-seed] seeded placeholder codex token at {auth.get_hermes_home() / 'auth.json'}")
    return 0


def main() -> int:
    """Run all sandbox-local boot seeds."""
    hermes_home = _hermes_home()
    workspace = Path(_required_env(name="HERMES_WEBUI_DEFAULT_WORKSPACE")).expanduser()
    aws_region = _required_env(name="AWS_DEFAULT_REGION")

    write_child_aws_config(workspace=workspace, aws_region=aws_region)
    seed_soul_file(hermes_home=hermes_home)
    configure_github_git_helper()
    return seed_codex_placeholder(hermes_home=hermes_home)


if __name__ == "__main__":
    sys.exit(main())
