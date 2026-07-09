"""Resolve the LLM provider/model preset for a deployed Hermes personal assistant.

A "preset" is a named bundle of HUMR_LLM_* env-var values (main + auxiliary
provider and model), chosen per-organization via Organization.llm_preset (which
defaults to Codex). The resolved values are applied as an override at
template-deploy time (see template_deploy_service.deploy_from_template),
replacing the template's hard-coded Bedrock defaults — so "which model this
customer gets" is set before they ever self-deploy.

Codex is the default; flip a specific org to Bedrock in the Django admin. There
is intentionally no env/global configuration — the per-org field is the only knob.
The Codex credential half (platform-shared ChatGPT login + broker token swap)
ships separately; see docs/integrations/device_flow_integration_design.md.
"""

from humanityrules_app import models

_DEFAULT_PRESET = models.Organization.LlmPreset.CODEX.value

# Bedrock preset model — matches the template's own default.
_BEDROCK_MODEL = "global.anthropic.claude-sonnet-4-6"

# Hermes' native ChatGPT-subscription provider. Inference flows through the
# broker, which swaps the platform-shared token onto the wire. Confirm the model
# id against the live Codex connection if it ever stops resolving.
_CODEX_PROVIDER = "openai-codex"
_CODEX_MAIN_MODEL = "gpt-5.5"
_CODEX_AUX_MODEL = "gpt-5.4-mini"

# Env vars the deploy form omits: the model is an org-level preset, not a
# per-deploy choice, so friends self-deploying never see (or can change) them.
# The base-url vars are hidden too — Codex needs none and Bedrock auto-derives
# its own in the container supervisor.
DEPLOY_FORM_HIDDEN_VARS = frozenset({
    "HUMR_LLM_PROVIDER", "HUMR_LLM_MODEL", "HUMR_LLM_BASE_URL",
    "HUMR_AUX_PROVIDER", "HUMR_AUX_MODEL", "HUMR_AUX_BASE_URL",
})

# Codex sets BOTH main and auxiliary to openai-codex on purpose: main and aux
# stay consistent with the org's single resolved preset rather than mixing
# providers within one deploy.
_PRESET_ENV: dict[str, dict[str, str]] = {
    models.Organization.LlmPreset.BEDROCK.value: {
        "HUMR_LLM_PROVIDER": "bedrock",
        "HUMR_LLM_MODEL": _BEDROCK_MODEL,
        "HUMR_AUX_PROVIDER": "bedrock",
        "HUMR_AUX_MODEL": _BEDROCK_MODEL,
    },
    models.Organization.LlmPreset.CODEX.value: {
        "HUMR_LLM_PROVIDER": _CODEX_PROVIDER,
        "HUMR_LLM_MODEL": _CODEX_MAIN_MODEL,
        "HUMR_AUX_PROVIDER": _CODEX_PROVIDER,
        "HUMR_AUX_MODEL": _CODEX_AUX_MODEL,
    },
}


def resolve_preset_name(organization: models.Organization) -> str:
    """Return the org's preset name, defaulting to Codex for a blank field."""
    return organization.llm_preset or _DEFAULT_PRESET


def llm_overrides_for(organization: models.Organization) -> dict[str, str]:
    """Return the HUMR_LLM_* env overrides for the org's resolved preset.

    An unrecognized preset name yields an empty dict, so the template's own
    defaults stand rather than producing a failed deploy.
    """
    overrides = _PRESET_ENV.get(resolve_preset_name(organization=organization))
    if overrides is None:
        return {}
    return dict(overrides)
