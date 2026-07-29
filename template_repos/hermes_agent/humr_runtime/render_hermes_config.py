"""Render Hermes config.yaml from the image template and the HUMR LLM preset.

Runs inside the nono sandbox as hermeswebui, invoked by webui.sh before the
system processes start. The inputs (preset name, region, capability grants)
are non-secret and the output lands in the agent-writable workspace, so
nothing here needs root.
"""

from __future__ import annotations

import os
from pathlib import Path

# Expand one stable HUMR preset into the concrete values rendered into
# config.yaml. The control plane persists only HUMR_LLM_PRESET in the app's
# container env; auxiliary slots ride on the same provider and model.
LLM_PRESETS: dict[str, dict[str, str]] = {
    "codex": {"provider": "openai-codex", "model": "gpt-5.5"},
    "bedrock": {"provider": "bedrock", "model": "global.anthropic.claude-sonnet-5"},
}

# The curated Bedrock model list appears in the WebUI dropdown exactly when
# the org holds the bedrock-runtime capability — the same grant that puts
# Bedrock actions on the ECS task role, so a listed model is always callable.
# Without it the placeholder line is dropped and Hermes keeps its own
# provider defaults.
BEDROCK_PROVIDERS_BLOCK = """\
providers:
  only_configured: false
  bedrock:
    models:
      'global.anthropic.claude-sonnet-5': "Sonnet 5"
      'global.anthropic.claude-opus-4-8': "Opus 4.8"
      'global.anthropic.claude-haiku-4-5-20251001-v1:0': "Haiku 4.5"
"""

PROVIDERS_BLOCK_PLACEHOLDER = "__PROVIDERS_BLOCK__"

# Image-owned template, shipped by the same Dockerfile that ships this script.
CONFIG_TEMPLATE_PATH = Path("/opt/hermes/config.yaml.template")


def has_platform_capability(granted: str, capability: str) -> bool:
    """True when `capability` is in the comma-separated grant list."""
    return capability in granted.split(",")


def render_config(template_text: str, preset: str, aws_region: str, platform_capabilities: str) -> str:
    """Render the config.yaml text for one deployment."""
    if preset not in LLM_PRESETS:
        supported = ", ".join(sorted(LLM_PRESETS))
        raise ValueError(f"Unsupported HUMR_LLM_PRESET '{preset}' (supported: {supported})")
    resolved = LLM_PRESETS[preset]

    base_url = ""
    if resolved["provider"] == "bedrock":
        base_url = f"https://bedrock-runtime.{aws_region}.amazonaws.com"

    bedrock_granted = has_platform_capability(granted=platform_capabilities, capability="bedrock-runtime")

    rendered_lines: list[str] = []
    for line in template_text.splitlines():
        if PROVIDERS_BLOCK_PLACEHOLDER in line:
            if bedrock_granted:
                rendered_lines.extend(BEDROCK_PROVIDERS_BLOCK.splitlines())
            continue
        rendered_lines.append(
            line.replace("__CONFIG_PROVIDER__", resolved["provider"])
            .replace("__MODEL__", resolved["model"])
            .replace("__BASE_URL__", base_url)
            .replace("__AUX_PROVIDER__", resolved["provider"])
            .replace("__AUX_MODEL__", resolved["model"])
            .replace("__AUX_BASE_URL__", "")
        )
    rendered = "\n".join(rendered_lines) + "\n"

    # Pins the region for Bedrock calls; Hermes's own defaults cover the key
    # when it is absent, so it rides along with the capability.
    if bedrock_granted:
        rendered += f"\nbedrock:\n  region: {aws_region}\n"

    return rendered


def _required_env(name: str) -> str:
    """Read a required environment variable."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def main() -> None:
    """Render the image config template into $HERMES_HOME/config.yaml."""
    template_text = CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
    hermes_home = Path(_required_env(name="HERMES_HOME")).expanduser()
    rendered = render_config(
        template_text=template_text,
        preset=_required_env(name="HUMR_LLM_PRESET"),
        aws_region=_required_env(name="AWS_DEFAULT_REGION"),
        platform_capabilities=os.environ.get("HUMR_PLATFORM_CAPABILITIES", ""),
    )
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
