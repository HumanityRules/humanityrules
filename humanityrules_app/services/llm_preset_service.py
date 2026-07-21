"""Resolve the LLM preset for a deployed Hermes personal assistant.

A preset is a stable HUMR-owned name chosen per organization. The control plane
persists only ``HUMR_LLM_PRESET`` in the app's materialized containers; the
Hermes container expands that value into concrete provider/model configuration
during startup. Model-version policy therefore stays out of the persisted
snapshot and takes effect on the next redeploy.

Codex is the default; flip a specific org to Bedrock in the Django admin. There
is intentionally no env/global configuration — the per-org field is the only knob.
The Codex credential half (platform-shared ChatGPT login + broker token swap)
ships separately; see docs/integrations/device_flow_integration_design.md.
"""

from humanityrules_app import models

_DEFAULT_PRESET = models.Organization.LlmPreset.CODEX.value
LLM_PRESET_ENV_VAR = "HUMR_LLM_PRESET"

DEPLOY_FORM_HIDDEN_VARS = frozenset({LLM_PRESET_ENV_VAR})


def resolve_preset_name(organization: models.Organization) -> str:
    """Return the org's preset name, defaulting to Codex for a blank field."""
    return organization.llm_preset or _DEFAULT_PRESET


def llm_overrides_for(organization: models.Organization) -> dict[str, str]:
    """Return the single persisted preset override for an organization."""
    preset = resolve_preset_name(organization=organization)
    if preset not in models.Organization.LlmPreset.values:
        raise ValueError(f"Unknown LLM preset: {preset!r}")
    return {LLM_PRESET_ENV_VAR: preset}
