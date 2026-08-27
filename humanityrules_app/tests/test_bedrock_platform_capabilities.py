"""Tests for the org-granted bedrock-runtime capability: task-role IAM, container env, config.yaml."""

import importlib.util
from pathlib import Path

import yaml
from aws_cdk import App
from aws_cdk.assertions import Template
from django.test import SimpleTestCase, TestCase

from humanityrules_app.management.commands import seed_app_templates
from humanityrules_app.models import AWSAccount, Environment, Organization, Workspace
from humanityrules_app.models import App as AppModel
from humanityrules_app.services.infra_customer import deploy_app
from humanityrules_app.services.infra_customer.appconfig import AppConfig, ContainerConfig, ImageSource
from humanityrules_app.services.jobs import app_config_builder
from humanityrules_app.tests.app_test_factories import make_source_template


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HERMES_RUNTIME_DIR = PROJECT_ROOT / "template_repos/hermes_agent/humr_runtime"
HERMES_CONFIG_RENDERER_PATH = HERMES_RUNTIME_DIR / "render_hermes_config.py"
HERMES_CONFIG_TEMPLATE_PATH = PROJECT_ROOT / "template_repos/hermes_agent/config.yaml.template"


def _app_config(preset: str, platform_capabilities: list[str]) -> AppConfig:
    return AppConfig(
        app_name="my-hermes",
        cpu=1024,
        memory=2048,
        alb_target_container="hermes",
        containers=[
            ContainerConfig(
                name="hermes",
                image_source=ImageSource.TEMPLATE,
                template_path="hermes_agent",
                container_port=8787,
                environment_variables=[
                    {"name": "HUMR_LLM_PRESET", "value": preset},
                ],
            ),
        ],
        platform_capabilities=platform_capabilities,
    )


def _bedrock_actions_from_stack(app_config: AppConfig) -> list[str]:
    cdk_app = App()
    stack = deploy_app.AppStack(
        scope=cdk_app,
        construct_id="TestAppStack",
        app_config=app_config,
        image_tags={"hermes_agent": "test"},
        env_slug="staging",
        resource_prefix="humr-staging-my-hermes",
        subdomain="myhermes",
        shared_alb_hosted_zone=None,
        app_secret_arn=None,
        auth_base_url=None,
    )
    template = Template.from_stack(stack).to_json()
    actions: list[str] = []

    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
            statement_actions = statement.get("Action", [])
            if isinstance(statement_actions, str):
                statement_actions = [statement_actions]
            actions.extend(action for action in statement_actions if action.startswith("bedrock:"))

    return sorted(actions)


def _load_container_config_renderer():
    """Import the container's render_hermes_config.py (stdlib-only) from the template repo."""
    spec = importlib.util.spec_from_file_location("render_hermes_config", HERMES_CONFIG_RENDERER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_hermes_config = _load_container_config_renderer()


def _render_hermes_config(preset: str, platform_capabilities: str) -> dict:
    """Render config.yaml with the container's real renderer and return it parsed."""
    rendered = render_hermes_config.render_config(
        template_text=HERMES_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8"),
        preset=preset,
        aws_region="us-east-1",
        platform_capabilities=platform_capabilities,
    )
    return yaml.safe_load(rendered)


class BedrockPlatformCapabilityTests(SimpleTestCase):

    def test_bedrock_preset_gets_broad_platform_task_role_grant(self) -> None:
        actions = _bedrock_actions_from_stack(
            _app_config(preset="bedrock", platform_capabilities=["bedrock-runtime"]),
        )

        self.assertEqual(actions, sorted(deploy_app.BEDROCK_RUNTIME_ACTIONS))

    def test_codex_preset_still_gets_bedrock_grant_when_capability_present(self) -> None:
        # The WebUI model dropdown offers Bedrock models to any granted org
        # regardless of its preset, so the grant follows the capability alone.
        actions = _bedrock_actions_from_stack(
            _app_config(preset="codex", platform_capabilities=["bedrock-runtime"]),
        )

        self.assertEqual(actions, sorted(deploy_app.BEDROCK_RUNTIME_ACTIONS))

    def test_bedrock_preset_without_the_capability_does_not_get_bedrock_grant(self) -> None:
        actions = _bedrock_actions_from_stack(
            _app_config(preset="bedrock", platform_capabilities=[]),
        )

        self.assertEqual(actions, [])

    def test_hermes_template_does_not_declare_bedrock_access_key_secrets(self) -> None:
        template = seed_app_templates.HERMES_PERSONAL_TEMPLATE
        secret_names = {
            var["name"]
            for container in template["containers"]
            for var in container.get("configurable_variables", [])
            if var["category"] == "secret"
        }

        self.assertEqual(template["default_compute_mode"], "ec2")
        self.assertNotIn("AWS_BEDROCK_ACCESS_KEY_ID", secret_names)
        self.assertNotIn("AWS_BEDROCK_SECRET_ACCESS_KEY", secret_names)

    def test_templates_declare_no_platform_capabilities(self) -> None:
        # The grant is an org entitlement; a template carrying one would be a
        # second, silently competing source of truth.
        for template in seed_app_templates.TEMPLATES:
            with self.subTest(template=template["slug"]):
                self.assertNotIn("platform_capabilities", template)

    def test_hermes_template_is_policy_proxy_fronted_with_checkpoint_efs(self) -> None:
        template = seed_app_templates.HERMES_PERSONAL_TEMPLATE

        self.assertEqual(template["slug"], "hermes-personal")
        self.assertEqual(template["default_compute_mode"], "ec2")
        self.assertEqual(template["efs_config"], {
            "mounts": [
                {
                    "name": "checkpoint",
                    "subpath": "checkpoint",
                    "container_path": "/hermes-checkpoint",
                    "posix_uid": 0,
                    "posix_gid": 0,
                },
            ],
        })
        self.assertEqual(template["alb_target_container"], "policy-proxy")
        self.assertEqual(template["serialize_task_replacement"], True)
        self.assertEqual(len(template["containers"]), 2)

        hermes = next(c for c in template["containers"] if c["name"] == "hermes")
        self.assertEqual(hermes["image_source"], "template")
        self.assertEqual(hermes["template_path"], "hermes_agent")
        self.assertEqual(hermes["efs_mounts"], ["checkpoint"])
        self.assertNotIn("depends_on", hermes)
        self.assertEqual(hermes["stop_timeout"], 600)
        # Reservations are resolved at deploy time from the node profile
        # (node_packing.resolve_slot_fillers); only the hard cap is declared.
        self.assertTrue(hermes["fills_node_slot"])
        self.assertNotIn("cpu_reservation", hermes)
        self.assertNotIn("memory_reservation_mib", hermes)
        self.assertEqual(hermes["memory_limit_mib"], 4096)

        variable_names = {var["name"] for var in hermes["configurable_variables"]}
        self.assertNotIn("HERMES_WEBUI_PASSWORD", variable_names)
        self.assertIn("HUMR_LLM_PRESET", variable_names)
        self.assertIn("AWS_DEFAULT_REGION", variable_names)
        # Tavily is a HUMR-managed shared credential injected by the integrations
        # broker, not a seeded per-app variable.
        self.assertNotIn("TAVILY_API_KEY", variable_names)
        self.assertNotIn("AWS_REGION", variable_names)
        self.assertNotIn("AWS_BEDROCK_REGION", variable_names)

        proxy = next(c for c in template["containers"] if c["name"] == "policy-proxy")
        self.assertEqual(proxy["image_source"], "template")
        self.assertEqual(proxy["template_path"], "policy_proxy")
        self.assertEqual(proxy["role"], "policy_proxy")
        self.assertEqual(proxy["upstream_container"], "hermes")
        self.assertEqual(proxy["cpu_reservation"], 32)
        self.assertEqual(proxy["memory_limit_mib"], 128)


class EffectivePlatformCapabilityTests(TestCase):
    """The config builder is the single point that turns plan entitlements into deploy capabilities."""

    def _make_app(self, llm_preset: str, bedrock_enabled: bool) -> AppModel:
        org = Organization.objects.create(
            name="Cap Org", slug="cap-org",
            llm_preset=llm_preset, plan_overrides={"bedrock_enabled": bedrock_enabled},
        )
        aws_account = AWSAccount.objects.create(organization=org, name="Cap AWS")
        workspace = Workspace.objects.create(organization=org, name="Engineering", slug="engineering")
        environment = Environment.objects.create(
            aws_account=aws_account, name="Staging", slug="staging",
            aws_region="us-east-1", status=Environment.Status.READY,
        )
        return AppModel.objects.create(
            organization=org, workspace=workspace, source_template=make_source_template(),
            environment=environment, name="MyApp", slug="myapp",
            container_port=8000, health_check_path="/health",
            cpu=256, memory=512,
        )

    def test_plan_override_passes_through_to_the_app_config(self) -> None:
        app = self._make_app(llm_preset="codex", bedrock_enabled=True)

        app_config = app_config_builder.build_app_config_from_app(app)

        self.assertEqual(app_config.platform_capabilities, ["bedrock-runtime"])

    def test_ungranted_org_deploys_with_no_capabilities(self) -> None:
        app = self._make_app(llm_preset="codex", bedrock_enabled=False)

        app_config = app_config_builder.build_app_config_from_app(app)

        self.assertEqual(app_config.platform_capabilities, [])

    def test_trial_plan_alone_grants_nothing(self) -> None:
        app = self._make_app(llm_preset="codex", bedrock_enabled=False)
        app.organization.plan_overrides = {}
        app.organization.save(update_fields=["plan_overrides"])

        app_config = app_config_builder.build_app_config_from_app(app)

        self.assertEqual(app_config.platform_capabilities, [])

    def test_bedrock_preset_with_the_grant_is_accepted(self) -> None:
        app = self._make_app(llm_preset="bedrock", bedrock_enabled=True)

        app_config = app_config_builder.build_app_config_from_app(app)

        self.assertEqual(app_config.platform_capabilities, ["bedrock-runtime"])

    def test_bedrock_preset_without_the_grant_fails_the_build(self) -> None:
        app = self._make_app(llm_preset="bedrock", bedrock_enabled=False)

        with self.assertRaises(app_config_builder.PlatformCapabilityNotGranted) as caught:
            app_config_builder.build_app_config_from_app(app)

        self.assertIn("cap-org", str(caught.exception))
        self.assertIn("bedrock-runtime", str(caught.exception))


class HermesConfigCapabilityGateTests(SimpleTestCase):
    """The container conditions its Bedrock config on the same capability as the IAM grant."""

    def test_granted_capability_offers_the_curated_bedrock_models(self) -> None:
        config = _render_hermes_config(preset="bedrock", platform_capabilities="bedrock-runtime")

        self.assertFalse(config["providers"]["only_configured"])
        self.assertEqual(
            set(config["providers"]["bedrock"]["models"]),
            {
                "global.anthropic.claude-sonnet-5",
                "global.anthropic.claude-opus-4-8",
                "global.anthropic.claude-haiku-4-5-20251001-v1:0",
            },
        )
        self.assertEqual(config["bedrock"]["region"], "us-east-1")

    def test_codex_preset_still_offers_bedrock_models_when_granted(self) -> None:
        config = _render_hermes_config(preset="codex", platform_capabilities="bedrock-runtime")

        self.assertIn("bedrock", config["providers"])
        self.assertEqual(config["model"]["provider"], "openai-codex")

    def test_ungranted_container_gets_no_bedrock_config(self) -> None:
        config = _render_hermes_config(preset="codex", platform_capabilities="")

        self.assertNotIn("providers", config)
        self.assertNotIn("bedrock", config)

    def test_unrelated_capability_does_not_unlock_bedrock(self) -> None:
        config = _render_hermes_config(preset="codex", platform_capabilities="bedrock-runtime-lookalike,other")

        self.assertNotIn("providers", config)
        self.assertNotIn("bedrock", config)
