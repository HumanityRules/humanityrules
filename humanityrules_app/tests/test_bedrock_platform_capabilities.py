"""Tests for platform-owned Bedrock task-role grants."""

from aws_cdk import App
from aws_cdk.assertions import Template
from django.test import SimpleTestCase

from humanityrules_app.management.commands import seed_app_templates
from humanityrules_app.services.infra_customer import deploy_app
from humanityrules_app.services.infra_customer.appconfig import AppConfig, ContainerConfig


def _app_config(preset: str, platform_capabilities: list[str]) -> AppConfig:
    return AppConfig(
        app_name="my-hermes",
        cpu=1024,
        memory=2048,
        alb_target_container="hermes",
        containers=[
            ContainerConfig(
                name="hermes",
                image_source="dockerfile",
                ecr_repo_name="humr/staging/my-hermes-hermes",
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
        image_tag="test",
        env_slug="staging",
        resource_prefix="humr-staging-my-hermes",
        subdomain="myhermes",
        database_connection_secret=None,
        shared_alb_hosted_zone=None,
        shared_hosted_zone_id=None,
        env_bearer_shared_secrets_arn=None,
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


class BedrockPlatformCapabilityTests(SimpleTestCase):

    def test_bedrock_preset_gets_broad_platform_task_role_grant(self) -> None:
        actions = _bedrock_actions_from_stack(
            _app_config(preset="bedrock", platform_capabilities=["bedrock-runtime"]),
        )

        self.assertEqual(actions, sorted(deploy_app.BEDROCK_RUNTIME_ACTIONS))

    def test_codex_preset_still_gets_bedrock_grant_when_capability_present(self) -> None:
        # The WebUI model dropdown always offers Bedrock models regardless of
        # the org's preset, so the grant follows the template capability alone.
        actions = _bedrock_actions_from_stack(
            _app_config(preset="codex", platform_capabilities=["bedrock-runtime"]),
        )

        self.assertEqual(actions, sorted(deploy_app.BEDROCK_RUNTIME_ACTIONS))

    def test_bedrock_preset_without_template_capability_does_not_get_bedrock_grant(self) -> None:
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

        self.assertEqual(template["platform_capabilities"], ["bedrock-runtime"])
        self.assertEqual(template["default_compute_mode"], "ec2")
        self.assertNotIn("AWS_BEDROCK_ACCESS_KEY_ID", secret_names)
        self.assertNotIn("AWS_BEDROCK_SECRET_ACCESS_KEY", secret_names)

    def test_hermes_template_is_policy_proxy_fronted_with_checkpoint_efs(self) -> None:
        template = seed_app_templates.HERMES_PERSONAL_TEMPLATE

        self.assertEqual(template["slug"], "hermes-personal")
        self.assertEqual(template["default_compute_mode"], "ec2")
        self.assertEqual(template["platform_capabilities"], ["bedrock-runtime"])
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
        self.assertEqual(hermes["image_source"], "dockerfile")
        self.assertEqual(hermes["source_repo_path"], "hermes_agent")
        self.assertEqual(hermes["dockerfile_path"], "Dockerfile")
        self.assertEqual(hermes["efs_mounts"], ["checkpoint"])
        self.assertNotIn("depends_on", hermes)
        self.assertEqual(hermes["stop_timeout"], 120)
        self.assertEqual(hermes["memory_reservation_mib"], 3328)
        self.assertEqual(hermes["memory_limit_mib"], 4096)
        self.assertEqual(hermes["cpu_reservation"], 896)

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
        self.assertEqual(proxy["image_source"], "policy_proxy")
        self.assertEqual(proxy["upstream_container"], "hermes")
        self.assertEqual(proxy["cpu_reservation"], 128)
        self.assertEqual(proxy["memory_limit_mib"], 256)
