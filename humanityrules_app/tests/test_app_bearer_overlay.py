"""Tests for the app bearer overlay in deploy_app.AppStack.

Verifies that containers talking to the control plane receive the
HUMR_APP_BEARER secret — mounted from the app's own Secrets Manager bag, not
from the environment's shared one — plus the HUMR_ENV_SLUG /
HUMR_CONTROL_PLANE_URL / HUMR_OWNER_USERNAME / HUMR_PUBLIC_HOSTNAME /
HUMR_PLATFORM_CAPABILITIES plain vars, and that other containers in the same
task do not. Also pins the task role to the app's own secret prefix, which is
what keeps one app's bearer unreachable from its neighbours.
"""

from aws_cdk import App
from aws_cdk.assertions import Template
from django.test import SimpleTestCase

from humanityrules_app.services.infra_customer import deploy_app
from humanityrules_app.services.infra_customer.appconfig import (
    AppConfig,
    ContainerConfig,
    ContainerRole,
    ImageSource,
)


APP_SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:humr/staging/my-app/secrets-abcdef"


def _render(
    containers: list[ContainerConfig],
    owner_username: str | None,
    org_slug: str | None,
    shared_alb_hosted_zone: str | None,
    platform_capabilities: list[str],
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
            org_slug=org_slug,
            platform_capabilities=platform_capabilities,
        ),
        image_tags={c.template_path: "test" for c in containers},
        env_slug="staging",
        resource_prefix="humr-staging-my-app",
        subdomain="myapp",
        shared_alb_hosted_zone=shared_alb_hosted_zone,
        app_secret_arn=APP_SECRET_ARN,
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


def _secrets_manager_resources(template: Template) -> list[str]:
    """Every resource a GetSecretValue statement in the stack's IAM policies grants on."""
    granted: list[str] = []
    for resource in template.to_json()["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if "secretsmanager:GetSecretValue" not in actions:
                continue
            statement_resources = statement.get("Resource", [])
            if not isinstance(statement_resources, list):
                statement_resources = [statement_resources]
            granted.extend(str(r) for r in statement_resources)
    return granted


class TestAppBearerOverlay(SimpleTestCase):

    def test_requires_app_bearer_container_gets_secret_and_env_vars(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="hermes",
                    image_source=ImageSource.TEMPLATE,
                    template_path="hermes_agent",
                    container_port=8787,
                    requires_app_bearer=True,
                ),
            ],
            owner_username="vmendi",
            org_slug="acme",
            shared_alb_hosted_zone=None,
            platform_capabilities=[],
        )

        hermes = _container_defs_by_name(template)["hermes"]
        env = {e["Name"]: e["Value"] for e in hermes.get("Environment", [])}
        self.assertEqual(env.get("HUMR_ENV_SLUG"), "staging")
        self.assertEqual(env.get("HUMR_OWNER_USERNAME"), "vmendi")
        self.assertEqual(env.get("HUMR_ORG_SLUG"), "acme")
        self.assertTrue(env.get("HUMR_CONTROL_PLANE_URL", "").startswith("https://"))
        # No shared_alb_hosted_zone passed → no HUMR_PUBLIC_HOSTNAME.
        self.assertNotIn("HUMR_PUBLIC_HOSTNAME", env)
        # Nothing granted still renders the var, so in-container gates read a
        # missing capability as denial rather than as unconfigured.
        self.assertEqual(env.get("HUMR_PLATFORM_CAPABILITIES"), "")
        secret_names = {s["Name"] for s in hermes.get("Secrets", [])}
        self.assertIn("HUMR_APP_BEARER", secret_names)

    def test_granted_capabilities_render_comma_separated(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="hermes",
                    image_source=ImageSource.TEMPLATE,
                    template_path="hermes_agent",
                    container_port=8787,
                    requires_app_bearer=True,
                ),
            ],
            owner_username="vmendi",
            org_slug="acme",
            shared_alb_hosted_zone=None,
            platform_capabilities=["bedrock-runtime", "some-future-capability"],
        )

        hermes = _container_defs_by_name(template)["hermes"]
        env = {e["Name"]: e["Value"] for e in hermes.get("Environment", [])}
        self.assertEqual(env.get("HUMR_PLATFORM_CAPABILITIES"), "bedrock-runtime,some-future-capability")

    def test_public_hostname_overlay_when_alb_hosted_zone_set(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="hermes",
                    image_source=ImageSource.TEMPLATE,
                    template_path="hermes_agent",
                    container_port=8787,
                    requires_app_bearer=True,
                ),
            ],
            owner_username="vmendi",
            org_slug="acme",
            shared_alb_hosted_zone="example.com",
            platform_capabilities=[],
        )

        hermes = _container_defs_by_name(template)["hermes"]
        env = {e["Name"]: e["Value"] for e in hermes.get("Environment", [])}
        # subdomain="myapp" + zone "example.com" → "myapp.example.com".
        self.assertEqual(env.get("HUMR_PUBLIC_HOSTNAME"), "myapp.example.com")

    def test_no_owner_username_omits_the_env_var(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="policy-proxy",
                    image_source=ImageSource.TEMPLATE,
                    template_path="policy_proxy",
                    container_port=8443,
                    requires_app_bearer=True,
                ),
            ],
            owner_username=None,
            org_slug=None,
            shared_alb_hosted_zone=None,
            platform_capabilities=[],
        )

        container = _container_defs_by_name(template)["policy-proxy"]
        env = {e["Name"]: e["Value"] for e in container.get("Environment", [])}
        self.assertEqual(env.get("HUMR_ENV_SLUG"), "staging")
        self.assertNotIn("HUMR_OWNER_USERNAME", env)
        self.assertNotIn("HUMR_ORG_SLUG", env)
        secret_names = {s["Name"] for s in container.get("Secrets", [])}
        self.assertIn("HUMR_APP_BEARER", secret_names)

    def test_policy_proxy_container_gets_bearer_without_requires_flag(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="policy-proxy",
                    image_source=ImageSource.TEMPLATE,
                    template_path="policy_proxy",
                    role=ContainerRole.POLICY_PROXY,
                    upstream_container="hermes",
                    container_port=8443,
                ),
                ContainerConfig(
                    name="hermes",
                    image_source=ImageSource.TEMPLATE,
                    template_path="hermes_agent",
                    container_port=8787,
                ),
            ],
            owner_username="vmendi",
            org_slug="acme",
            shared_alb_hosted_zone=None,
            platform_capabilities=[],
        )

        containers = _container_defs_by_name(template)
        proxy = containers["policy-proxy"]
        proxy_env = {e["Name"]: e["Value"] for e in proxy.get("Environment", [])}
        self.assertEqual(proxy_env.get("HUMR_ENV_SLUG"), "staging")
        self.assertEqual(proxy_env.get("HUMR_OWNER_USERNAME"), "vmendi")
        proxy_secret_names = {s["Name"] for s in proxy.get("Secrets", [])}
        self.assertIn("HUMR_APP_BEARER", proxy_secret_names)

        hermes = containers["hermes"]
        hermes_env = {e["Name"]: e["Value"] for e in hermes.get("Environment", [])}
        self.assertNotIn("HUMR_ENV_SLUG", hermes_env)
        hermes_secret_names = {s["Name"] for s in hermes.get("Secrets", [])}
        self.assertNotIn("HUMR_APP_BEARER", hermes_secret_names)

    def test_container_without_flag_gets_no_overlay(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="hermes",
                    image_source=ImageSource.TEMPLATE,
                    template_path="hermes_agent",
                    container_port=8787,
                    requires_app_bearer=True,
                ),
                ContainerConfig(
                    name="docker-dind",
                    image_source=ImageSource.TEMPLATE,
                    template_path="docker_dind",
                    container_port=0,
                    requires_app_bearer=False,
                ),
            ],
            owner_username="vmendi",
            org_slug="acme",
            shared_alb_hosted_zone=None,
            platform_capabilities=[],
        )

        dind = _container_defs_by_name(template)["docker-dind"]
        env = {e["Name"]: e["Value"] for e in dind.get("Environment", [])}
        self.assertNotIn("HUMR_ENV_SLUG", env)
        self.assertNotIn("HUMR_OWNER_USERNAME", env)
        self.assertNotIn("HUMR_CONTROL_PLANE_URL", env)
        self.assertNotIn("HUMR_PLATFORM_CAPABILITIES", env)
        secret_names = {s["Name"] for s in dind.get("Secrets", [])}
        self.assertNotIn("HUMR_APP_BEARER", secret_names)

    def test_task_role_reads_only_this_app_secret_prefix(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="hermes",
                    image_source=ImageSource.TEMPLATE,
                    template_path="hermes_agent",
                    container_port=8787,
                    requires_app_bearer=True,
                ),
            ],
            owner_username="vmendi",
            org_slug="acme",
            shared_alb_hosted_zone=None,
            platform_capabilities=[],
        )

        granted = _secrets_manager_resources(template)
        self.assertEqual(len(granted), 1)
        self.assertIn("humr/staging/my-app/*", granted[0])
        # The environment's shared bag is out of reach: it is what used to hold
        # one bearer for every app in the environment.
        self.assertNotIn("shared-secrets", granted[0])

    def test_bearer_secret_points_at_the_app_bag(self) -> None:
        template = _render(
            containers=[
                ContainerConfig(
                    name="hermes",
                    image_source=ImageSource.TEMPLATE,
                    template_path="hermes_agent",
                    container_port=8787,
                    requires_app_bearer=True,
                ),
            ],
            owner_username="vmendi",
            org_slug="acme",
            shared_alb_hosted_zone=None,
            platform_capabilities=[],
        )

        hermes = _container_defs_by_name(template)["hermes"]
        bearer = next(s for s in hermes["Secrets"] if s["Name"] == "HUMR_APP_BEARER")
        self.assertEqual(bearer["ValueFrom"], f"{APP_SECRET_ARN}:HUMR_APP_BEARER::")
