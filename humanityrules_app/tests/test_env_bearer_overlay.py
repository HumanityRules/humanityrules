"""Tests for the env-bearer overlay in deploy_app.AppStack.

Verifies that containers needing env-bearer receive the HUMR_ENV_BEARER
secret and the HUMR_ENV_SLUG / HUMR_CONTROL_PLANE_URL / HUMR_OWNER_USERNAME /
HUMR_PUBLIC_HOSTNAME plain vars, and that other containers in the same task
do not.
"""

from aws_cdk import App
from aws_cdk.assertions import Template
from django.test import SimpleTestCase

from humanityrules_app.services.infra_customer import deploy_app
from humanityrules_app.services.infra_customer.appconfig import (
    AppConfig,
    ContainerConfig,
)


SHARED_SECRETS_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:devopshero/staging/shared-secrets-abcdef"


def _render(
    containers: list[ContainerConfig],
    owner_username: str | None,
    shared_alb_hosted_zone: str | None = None,
) -> Template:
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
        shared_alb_hosted_zone=shared_alb_hosted_zone,
        shared_hosted_zone_id=None,
        env_bearer_shared_secrets_arn=SHARED_SECRETS_ARN,
        auth_base_url="https://humanityrules.io",
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
        self.assertEqual(env.get("HUMR_ENV_SLUG"), "staging")
        self.assertEqual(env.get("HUMR_OWNER_USERNAME"), "vmendi")
        self.assertTrue(env.get("HUMR_CONTROL_PLANE_URL", "").startswith("https://"))
        # No shared_alb_hosted_zone passed → no HUMR_PUBLIC_HOSTNAME (path-based routing).
        self.assertNotIn("HUMR_PUBLIC_HOSTNAME", env)
        secret_names = {s["Name"] for s in hermes.get("Secrets", [])}
        self.assertIn("HUMR_ENV_BEARER", secret_names)

    def test_public_hostname_overlay_when_alb_hosted_zone_set(self) -> None:
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
            shared_alb_hosted_zone="example.com",
        )

        hermes = _container_defs_by_name(template)["hermes"]
        env = {e["Name"]: e["Value"] for e in hermes.get("Environment", [])}
        # subdomain="my-app" + zone "example.com" → "my-app.example.com".
        self.assertEqual(env.get("HUMR_PUBLIC_HOSTNAME"), "my-app.example.com")

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
        self.assertEqual(env.get("HUMR_ENV_SLUG"), "staging")
        self.assertNotIn("HUMR_OWNER_USERNAME", env)
        secret_names = {s["Name"] for s in container.get("Secrets", [])}
        self.assertIn("HUMR_ENV_BEARER", secret_names)

    def test_policy_proxy_container_gets_bearer_without_requires_flag(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="policy-proxy",
                    image_source="policy_proxy",
                    upstream_container="hermes",
                    container_port=8443,
                ),
                ContainerConfig(
                    name="hermes",
                    image_source="dockerfile",
                    source_repo_path="hermes_agent",
                    ecr_repo_name="doh/staging/my-app-hermes",
                    container_port=8787,
                ),
            ],
            owner_username="vmendi",
        )

        containers = _container_defs_by_name(template)
        proxy = containers["policy-proxy"]
        proxy_env = {e["Name"]: e["Value"] for e in proxy.get("Environment", [])}
        self.assertEqual(proxy_env.get("HUMR_ENV_SLUG"), "staging")
        self.assertEqual(proxy_env.get("HUMR_OWNER_USERNAME"), "vmendi")
        proxy_secret_names = {s["Name"] for s in proxy.get("Secrets", [])}
        self.assertIn("HUMR_ENV_BEARER", proxy_secret_names)

        hermes = containers["hermes"]
        hermes_env = {e["Name"]: e["Value"] for e in hermes.get("Environment", [])}
        self.assertNotIn("HUMR_ENV_SLUG", hermes_env)
        hermes_secret_names = {s["Name"] for s in hermes.get("Secrets", [])}
        self.assertNotIn("HUMR_ENV_BEARER", hermes_secret_names)

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
        self.assertNotIn("HUMR_ENV_SLUG", env)
        self.assertNotIn("HUMR_OWNER_USERNAME", env)
        self.assertNotIn("HUMR_CONTROL_PLANE_URL", env)
        secret_names = {s["Name"] for s in dind.get("Secrets", [])}
        self.assertNotIn("HUMR_ENV_BEARER", secret_names)
