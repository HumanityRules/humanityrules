"""Tests for the per-org LLM preset (Codex default, Bedrock selectable in admin).

Covers preset resolution, the deploy-time override application (mirroring
template_deploy_service.deploy_from_template's sequence), the deploy-form filter
that hides the model vars, and the field default flowing through onboarding.
"""

from django.test import SimpleTestCase, TestCase

from humanityrules_app import models
from humanityrules_app.management.commands import seed_app_templates
from humanityrules_app.services import llm_preset_service
from humanityrules_app.services.app_templates import template_deploy_service
from humanityrules_app.views import template_deploy

CODEX_MODEL = "gpt-5.5"


class LlmPresetResolutionTests(SimpleTestCase):

    def test_blank_preset_resolves_to_codex(self) -> None:
        org = models.Organization(llm_preset="")
        self.assertEqual(llm_preset_service.resolve_preset_name(organization=org), "codex")

    def test_explicit_preset_is_kept(self) -> None:
        org = models.Organization(llm_preset="bedrock")
        self.assertEqual(llm_preset_service.resolve_preset_name(organization=org), "bedrock")

    def test_codex_overrides_set_both_main_and_aux(self) -> None:
        org = models.Organization(llm_preset="codex")
        self.assertEqual(llm_preset_service.llm_overrides_for(organization=org), {
            "HUMR_LLM_PROVIDER": "openai-codex",
            "HUMR_LLM_MODEL": CODEX_MODEL,
            "HUMR_AUX_PROVIDER": "openai-codex",
            "HUMR_AUX_MODEL": CODEX_MODEL,
        })

    def test_bedrock_overrides_set_both_main_and_aux(self) -> None:
        org = models.Organization(llm_preset="bedrock")
        overrides = llm_preset_service.llm_overrides_for(organization=org)
        self.assertEqual(overrides["HUMR_LLM_PROVIDER"], "bedrock")
        self.assertEqual(overrides["HUMR_AUX_PROVIDER"], "bedrock")

    def test_unknown_preset_yields_no_overrides(self) -> None:
        org = models.Organization(llm_preset="gemini")
        self.assertEqual(llm_preset_service.llm_overrides_for(organization=org), {})


class LlmPresetDeployApplicationTests(SimpleTestCase):
    """The preset must reach the materialized hermes blueprint container env."""

    def _hermes_env(self, preset: str, extra_overrides: dict[str, str]) -> dict[str, str]:
        org = models.Organization(llm_preset=preset)
        # Mirror deploy_from_template: preset first, explicit caller overrides after.
        containers = template_deploy_service._apply_variable_overrides(
            seed_app_templates.HERMES_PERSONAL_TEMPLATE["containers"],
            llm_preset_service.llm_overrides_for(organization=org),
        )
        containers = template_deploy_service._apply_variable_overrides(containers, extra_overrides)
        materialized = template_deploy_service._materialize_blueprint_containers(containers)
        hermes = next(c for c in materialized if c["name"] == "hermes")
        return {e["name"]: e["value"] for e in hermes["environment_variables"]}

    def test_codex_preset_reaches_blueprint_env(self) -> None:
        env = self._hermes_env(preset="codex", extra_overrides={})
        self.assertEqual(env["HUMR_LLM_PROVIDER"], "openai-codex")
        self.assertEqual(env["HUMR_AUX_PROVIDER"], "openai-codex")
        self.assertEqual(env["HUMR_LLM_MODEL"], CODEX_MODEL)

    def test_blank_preset_defaults_to_codex(self) -> None:
        env = self._hermes_env(preset="", extra_overrides={})
        self.assertEqual(env["HUMR_LLM_PROVIDER"], "openai-codex")

    def test_bedrock_preset_reaches_blueprint_env(self) -> None:
        env = self._hermes_env(preset="bedrock", extra_overrides={})
        self.assertEqual(env["HUMR_LLM_PROVIDER"], "bedrock")
        self.assertEqual(env["HUMR_AUX_PROVIDER"], "bedrock")

    def test_explicit_runtime_override_wins_over_preset(self) -> None:
        env = self._hermes_env(preset="codex", extra_overrides={"HUMR_LLM_PROVIDER": "bedrock"})
        self.assertEqual(env["HUMR_LLM_PROVIDER"], "bedrock")
        # Aux is untouched by the override, so it still reflects the preset.
        self.assertEqual(env["HUMR_AUX_PROVIDER"], "openai-codex")


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
        self.assertNotIn("HUMR_LLM_PROVIDER", shown)
        self.assertNotIn("HUMR_AUX_MODEL", shown)


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
