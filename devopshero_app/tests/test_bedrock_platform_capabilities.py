"""Tests for platform-owned Bedrock task-role grants."""

from aws_cdk import App
from aws_cdk.assertions import Template
from django.test import SimpleTestCase

from devopshero_app.management.commands import seed_app_templates
from devopshero_app.services.infra_customer import deploy_app
from devopshero_app.services.infra_customer.appconfig import AppConfig, ContainerConfig


def _app_config(provider: str, platform_capabilities: list[str]) -> AppConfig:
    return AppConfig(
        app_name="my-hermes",
        cpu=1024,
        memory=2048,
        alb_target_container="hermes",
        containers=[
            ContainerConfig(
                name="hermes",
                image_source="dockerfile",
                ecr_repo_name="doh/staging/my-hermes-hermes",
                container_port=8787,
                environment_variables=[
                    {"name": "DOH_LLM_PROVIDER", "value": provider},
                    {"name": "DOH_LLM_MODEL", "value": "us.anthropic.claude-opus-4-7"},
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
        resource_prefix="doh-staging-my-hermes",
        subdomain="my-hermes",
        database_connection_secret=None,
        shared_alb_hosted_zone=None,
        shared_hosted_zone_id=None,
        sidecar_shared_secrets_arn=None,
        sidecar_image_version=None,
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

    def test_bedrock_provider_gets_broad_platform_task_role_grant(self) -> None:
        actions = _bedrock_actions_from_stack(
            _app_config(provider="bedrock", platform_capabilities=["bedrock-runtime"]),
        )

        self.assertEqual(actions, sorted(deploy_app.BEDROCK_RUNTIME_ACTIONS))

    def test_custom_provider_does_not_get_bedrock_grant(self) -> None:
        actions = _bedrock_actions_from_stack(
            _app_config(provider="custom", platform_capabilities=["bedrock-runtime"]),
        )

        self.assertEqual(actions, [])

    def test_bedrock_provider_without_template_capability_does_not_get_bedrock_grant(self) -> None:
        actions = _bedrock_actions_from_stack(
            _app_config(provider="bedrock", platform_capabilities=[]),
        )

        self.assertEqual(actions, [])

    def test_hermes_templates_do_not_declare_bedrock_access_key_secrets(self) -> None:
        for template in [
            seed_app_templates.HERMES_PERSONAL_TEMPLATE,
            seed_app_templates.HERMES_SLACK_TEMPLATE,
        ]:
            secret_names = {
                var["name"]
                for container in template["containers"]
                for var in container.get("runtime_variables", [])
                if var["category"] == "secret"
            }

            self.assertEqual(template["platform_capabilities"], ["bedrock-runtime"])
            self.assertEqual(template["default_compute_mode"], "ec2")
            self.assertNotIn("AWS_BEDROCK_ACCESS_KEY_ID", secret_names)
            self.assertNotIn("AWS_BEDROCK_SECRET_ACCESS_KEY", secret_names)

    def test_hermes_templates_enable_docker_backed_tools(self) -> None:
        for template in [
            seed_app_templates.HERMES_PERSONAL_TEMPLATE,
            seed_app_templates.HERMES_SLACK_TEMPLATE,
        ]:
            hermes_container = next(c for c in template["containers"] if c["name"] == "hermes")
            runtime_vars = {var["name"]: var for var in hermes_container["runtime_variables"]}

            self.assertEqual(template["efs_config"]["docker_workspace_subpath"], "workspace")
            self.assertNotIn("user", hermes_container)
            self.assertEqual(hermes_container["host_mounts"], [
                {
                    "source_path": "/var/run/docker.sock",
                    "container_path": "/var/run/docker.sock",
                    "read_only": False,
                },
            ])
            self.assertEqual(runtime_vars["DOH_HERMES_REQUIRE_DOCKER"]["value"], "1")
