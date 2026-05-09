"""Tests for the env-bearer overlay in deploy_app.AppStack.

Verifies that containers with `requires_env_bearer=True` receive the
DOH_ENV_BEARER secret and the DOH_ENV_SLUG / DOH_OWNER_USERNAME plain vars, and
that other containers in the same task do not.
"""

from aws_cdk import App
from aws_cdk.assertions import Template
from django.test import SimpleTestCase

from devopshero_app.services.infra_customer import deploy_app
from devopshero_app.services.infra_customer.appconfig import (
    AppConfig,
    ContainerConfig,
)


SHARED_SECRETS_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:devopshero/staging/shared-secrets-abcdef"


def _render(containers: list[ContainerConfig], owner_username: str | None) -> Template:
    cdk_app = App()
    stack = deploy_app.AppStack(
        scope=cdk_app,
        construct_id="TestAppStack",
        app_config=AppConfig(
            app_name="my-app",
            cpu=512,
            memory=1024,
            compute_mode="fargate",
            alb_target_container=containers[0].name,
            containers=containers,
            owner_username=owner_username,
        ),
        image_tag="test",
        env_slug="staging",
        resource_prefix="doh-staging-my-app",
        subdomain="my-app",
        database_connection_secret=None,
        shared_alb_hosted_zone=None,
        shared_hosted_zone_id=None,
        env_bearer_shared_secrets_arn=SHARED_SECRETS_ARN,
        auth_base_url=None,
    )
    return Template.from_stack(stack)


APP_NAME = "my-app"


def _container_defs_by_name(template: Template) -> dict[str, dict]:
    """Extract the CDK-rendered ECS container definitions keyed by their *template* name.

    CDK prefixes container names with the app name (e.g. "my-app-hermes"); we
    strip that so tests can look up by the configured container name ("hermes").
    """
    resources = template.to_json()["Resources"]
    for resource in resources.values():
        if resource["Type"] != "AWS::ECS::TaskDefinition":
            continue
        container_defs = resource["Properties"]["ContainerDefinitions"]
        prefix = f"{APP_NAME}-"
        out: dict[str, dict] = {}
        for c in container_defs:
            name = c["Name"]
            if name.startswith(prefix):
                name = name[len(prefix):]
            out[name] = c
        return out
    raise AssertionError("No ECS::TaskDefinition resource found in stack")


class TestEnvBearerOverlay(SimpleTestCase):

    def test_requires_env_bearer_container_gets_secret_and_env_vars(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="hermes",
                    image_source="dockerfile",
                    source_repo_path="hermes_agent",
                    ecr_repo_name="doh/staging/my-app-hermes",
                    container_port=8787,
                    requires_env_bearer=True,
                ),
            ],
            owner_username="vmendi",
        )

        hermes = _container_defs_by_name(template)["hermes"]
        env = {e["Name"]: e["Value"] for e in hermes.get("Environment", [])}
        self.assertEqual(env.get("DOH_ENV_SLUG"), "staging")
        self.assertEqual(env.get("DOH_OWNER_USERNAME"), "vmendi")
        secret_names = {s["Name"] for s in hermes.get("Secrets", [])}
        self.assertIn("DOH_ENV_BEARER", secret_names)

    def test_no_owner_username_omits_the_env_var(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="policy-proxy",
                    image_source="dockerfile",
                    source_repo_path="policy_proxy",
                    ecr_repo_name="doh/staging/my-app-policy-proxy",
                    container_port=8443,
                    requires_env_bearer=True,
                ),
            ],
            owner_username=None,
        )

        container = _container_defs_by_name(template)["policy-proxy"]
        env = {e["Name"]: e["Value"] for e in container.get("Environment", [])}
        self.assertEqual(env.get("DOH_ENV_SLUG"), "staging")
        self.assertNotIn("DOH_OWNER_USERNAME", env)
        secret_names = {s["Name"] for s in container.get("Secrets", [])}
        self.assertIn("DOH_ENV_BEARER", secret_names)

    def test_container_without_flag_gets_no_overlay(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="hermes",
                    image_source="dockerfile",
                    source_repo_path="hermes_agent",
                    ecr_repo_name="doh/staging/my-app-hermes",
                    container_port=8787,
                    requires_env_bearer=True,
                ),
                ContainerConfig(
                    name="docker-dind",
                    image_source="registry",
                    registry_image="docker:26.1.0-dind",
                    container_port=0,
                    requires_env_bearer=False,
                ),
            ],
            owner_username="vmendi",
        )

        dind = _container_defs_by_name(template)["docker-dind"]
        env = {e["Name"]: e["Value"] for e in dind.get("Environment", [])}
        self.assertNotIn("DOH_ENV_SLUG", env)
        self.assertNotIn("DOH_OWNER_USERNAME", env)
        secret_names = {s["Name"] for s in dind.get("Secrets", [])}
        self.assertNotIn("DOH_ENV_BEARER", secret_names)
