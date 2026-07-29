"""Tests for the per-org LLM preset (Codex default, Bedrock selectable in admin).

Covers preset resolution, the deploy-time override application (mirroring
template_deploy_service.deploy_from_template's sequence), the deploy-form filter
that hides the model vars, and the field default flowing through onboarding.
"""

import ast
import importlib.util
from pathlib import Path

import yaml
from django.test import SimpleTestCase, TestCase

from humanityrules_app import models
from humanityrules_app.management.commands import seed_app_templates
from humanityrules_app.services import llm_preset_service
from humanityrules_app.services import template_deploy_service
from humanityrules_app.views import template_deploy

CODEX_MAIN_MODEL = "gpt-5.5"
BEDROCK_MODEL = "global.anthropic.claude-sonnet-5"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
HERMES_CONFIG_TEMPLATE_PATH = PROJECT_ROOT / "template_repos/hermes_agent/config.yaml.template"
HERMES_CONFIG_SOURCE_PATH = PROJECT_ROOT / "template_repos/hermes_agent/vendor/hermes-agent/hermes_cli/config.py"
HERMES_CONFIG_RENDERER_PATH = PROJECT_ROOT / "template_repos/hermes_agent/humr_runtime/render_hermes_config.py"
LEGACY_LLM_ENV_VARS = {
    "HUMR_LLM_PROVIDER",
    "HUMR_LLM_MODEL",
    "HUMR_LLM_BASE_URL",
    "HUMR_AUX_PROVIDER",
    "HUMR_AUX_MODEL",
    "HUMR_AUX_BASE_URL",
}
MAIN_MODEL_AUXILIARY_SLOTS = (
    "kanban_decomposer",
    "curator",
    "background_review",
    "moa_aggregator",
)


def _hermes_default_auxiliary_slots() -> set[str]:
    """Read the vendored DEFAULT_CONFIG auxiliary model slots without importing Hermes."""
    config_module = ast.parse(HERMES_CONFIG_SOURCE_PATH.read_text(encoding="utf-8"))
    for statement in config_module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "DEFAULT_CONFIG" for target in statement.targets):
            continue
        if not isinstance(statement.value, ast.Dict):
            break
        for key, value in zip(statement.value.keys, statement.value.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "auxiliary" and isinstance(value, ast.Dict):
                slots: set[str] = set()
                for slot, slot_config in zip(value.keys, value.values, strict=True):
                    if not (
                        isinstance(slot, ast.Constant)
                        and isinstance(slot.value, str)
                        and isinstance(slot_config, ast.Dict)
                    ):
                        continue
                    config_keys = {
                        config_key.value
                        for config_key in slot_config.keys
                        if isinstance(config_key, ast.Constant) and isinstance(config_key.value, str)
                    }
                    if {"provider", "model"}.issubset(config_keys):
                        slots.add(slot.value)
                return slots
        break
    raise AssertionError("Could not find DEFAULT_CONFIG['auxiliary'] in vendored Hermes")


def _load_container_config_renderer():
    """Import the container's render_hermes_config.py (stdlib-only) from the template repo."""
    spec = importlib.util.spec_from_file_location("render_hermes_config", HERMES_CONFIG_RENDERER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_hermes_config = _load_container_config_renderer()


class LlmPresetResolutionTests(SimpleTestCase):

    def test_blank_preset_resolves_to_codex(self) -> None:
        org = models.Organization(llm_preset="")
        self.assertEqual(llm_preset_service.resolve_preset_name(organization=org), "codex")

    def test_explicit_preset_is_kept(self) -> None:
        org = models.Organization(llm_preset="bedrock")
        self.assertEqual(llm_preset_service.resolve_preset_name(organization=org), "bedrock")

    def test_codex_override_persists_only_the_preset(self) -> None:
        org = models.Organization(llm_preset="codex")
        self.assertEqual(
            llm_preset_service.llm_overrides_for(organization=org),
            {"HUMR_LLM_PRESET": "codex"},
        )

    def test_bedrock_override_persists_only_the_preset(self) -> None:
        org = models.Organization(llm_preset="bedrock")
        self.assertEqual(
            llm_preset_service.llm_overrides_for(organization=org),
            {"HUMR_LLM_PRESET": "bedrock"},
        )

    def test_unknown_preset_is_rejected(self) -> None:
        org = models.Organization(llm_preset="gemini")
        with self.assertRaisesMessage(ValueError, "Unknown LLM preset: 'gemini'"):
            llm_preset_service.llm_overrides_for(organization=org)


class LlmPresetDeployApplicationTests(SimpleTestCase):
    """The preset must reach the materialized hermes App container env."""

    def _hermes_env(self, preset: str, extra_overrides: dict[str, str]) -> dict[str, str]:
        org = models.Organization(llm_preset=preset)
        # Mirror deploy_from_template: preset first, explicit caller overrides after.
        containers = template_deploy_service._apply_variable_overrides(
            seed_app_templates.HERMES_PERSONAL_TEMPLATE["containers"],
            llm_preset_service.llm_overrides_for(organization=org),
        )
        containers = template_deploy_service._apply_variable_overrides(containers, extra_overrides)
        materialized = template_deploy_service._materialize_app_containers(containers)
        hermes = next(c for c in materialized if c["name"] == "hermes")
        return {e["name"]: e["value"] for e in hermes["environment_variables"]}

    def test_codex_preset_reaches_app_container_env(self) -> None:
        env = self._hermes_env(preset="codex", extra_overrides={})
        self.assertEqual(env["HUMR_LLM_PRESET"], "codex")
        self.assertTrue(LEGACY_LLM_ENV_VARS.isdisjoint(env))

    def test_blank_preset_defaults_to_codex(self) -> None:
        env = self._hermes_env(preset="", extra_overrides={})
        self.assertEqual(env["HUMR_LLM_PRESET"], "codex")

    def test_bedrock_preset_reaches_app_container_env(self) -> None:
        env = self._hermes_env(preset="bedrock", extra_overrides={})
        self.assertEqual(env["HUMR_LLM_PRESET"], "bedrock")
        self.assertTrue(LEGACY_LLM_ENV_VARS.isdisjoint(env))

    def test_explicit_runtime_override_wins_over_preset(self) -> None:
        env = self._hermes_env(preset="codex", extra_overrides={"HUMR_LLM_PRESET": "bedrock"})
        self.assertEqual(env["HUMR_LLM_PRESET"], "bedrock")


class HermesPresetResolverTests(SimpleTestCase):

    def test_codex_resolves_to_gpt_5_5(self) -> None:
        resolved = render_hermes_config.LLM_PRESETS["codex"]

        self.assertEqual(resolved["provider"], "openai-codex")
        self.assertEqual(resolved["model"], CODEX_MAIN_MODEL)

    def test_bedrock_resolves_to_sonnet(self) -> None:
        resolved = render_hermes_config.LLM_PRESETS["bedrock"]

        self.assertEqual(resolved["provider"], "bedrock")
        self.assertEqual(resolved["model"], BEDROCK_MODEL)

    def test_resolver_supports_every_control_plane_preset(self) -> None:
        for preset in models.Organization.LlmPreset.values:
            with self.subTest(preset=preset):
                self.assertIn(preset, render_hermes_config.LLM_PRESETS)

    def test_unknown_preset_is_rejected(self) -> None:
        with self.assertRaisesMessage(ValueError, "Unsupported HUMR_LLM_PRESET 'gemini'"):
            render_hermes_config.render_config(
                template_text=HERMES_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8"),
                preset="gemini",
                aws_region="us-east-1",
                platform_capabilities="",
            )


class HermesAuxiliaryConfigTemplateTests(SimpleTestCase):

    def test_template_pins_every_auxiliary_slot(self) -> None:
        rendered = render_hermes_config.render_config(
            template_text=HERMES_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8"),
            preset="codex",
            aws_region="us-east-1",
            platform_capabilities="",
        )
        config = yaml.safe_load(rendered)
        auxiliary = config["auxiliary"]
        expected_slots = _hermes_default_auxiliary_slots() | {"goal_judge"}

        self.assertEqual(set(auxiliary), expected_slots)
        self.assertEqual(list(auxiliary)[-len(MAIN_MODEL_AUXILIARY_SLOTS):], list(MAIN_MODEL_AUXILIARY_SLOTS))
        for slot, slot_config in auxiliary.items():
            self.assertEqual(slot_config["provider"], "openai-codex")
            self.assertEqual(slot_config["model"], CODEX_MAIN_MODEL)


class HermesModelCatalogTests(SimpleTestCase):

    def test_sonnet_5_is_the_only_curated_sonnet_model(self) -> None:
        curated_models = render_hermes_config.BEDROCK_PROVIDERS_BLOCK
        sonnet_lines = [line.strip() for line in curated_models.splitlines() if "sonnet" in line.lower()]

        self.assertEqual(sonnet_lines, [f"'{BEDROCK_MODEL}': \"Sonnet 5\""])


class LlmPresetDeployFormTests(SimpleTestCase):

    def test_only_llm_vars_are_hidden_from_deploy_form(self) -> None:
        containers = seed_app_templates.HERMES_PERSONAL_TEMPLATE["containers"]
        template = models.AppTemplate(containers=containers)
        raw_editable = {
            v["name"]
            for c in containers
            for v in c.get("configurable_variables", [])
            if v.get("user_editable")
        }
        shown = {v["name"] for v in template_deploy._editable_variables(template)}

        self.assertEqual(raw_editable - shown, raw_editable & llm_preset_service.DEPLOY_FORM_HIDDEN_VARS)
        self.assertNotIn("HUMR_LLM_PRESET", shown)
        self.assertTrue(LEGACY_LLM_ENV_VARS.isdisjoint(raw_editable))


class LlmPresetOnboardingDefaultTests(TestCase):
    """Self-serve onboarding (each friend creates their own org) gets the Codex default."""

    def _seed_pending_workos_session(self, email: str) -> None:
        session = self.client.session
        session["pending_workos_user"] = {
            "workos_user_id": "user_default123",
            "email": email,
            "first_name": "New",
            "last_name": "Friend",
        }
        session.save()

    def test_new_org_defaults_to_codex(self) -> None:
        self._seed_pending_workos_session(email="friend@example.com")

        response = self.client.post("/onboarding/", {"organization_name": "Friend Co"})

        self.assertEqual(response.status_code, 302)
        org = models.Organization.objects.get(slug="friend-co")
        self.assertEqual(org.llm_preset, "codex")
