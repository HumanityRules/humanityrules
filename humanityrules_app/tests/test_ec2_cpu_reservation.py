"""Tests for EC2 container-level CPU reservation in deploy_app.AppStack."""

from aws_cdk import App
from aws_cdk.assertions import Template
from django.test import SimpleTestCase

from humanityrules_app.services.infra_customer import deploy_app
from humanityrules_app.services.infra_customer.appconfig import AppConfig, ContainerConfig, ContainerRole, ImageSource


def _task_definition_properties(template: Template) -> dict:
    resources = template.to_json()["Resources"]
    for resource in resources.values():
        if resource["Type"] == "AWS::ECS::TaskDefinition":
            return resource["Properties"]
    raise AssertionError("No ECS::TaskDefinition resource found in stack")


SHARED_SECRETS_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:humr/staging/shared-secrets-abcdef"


def _render_ec2(containers: list[ContainerConfig], cpu: int, alb_target_container: str, serialize_task_replacement: bool) -> Template:
    cdk_app = App()
    stack = deploy_app.AppStack(
        scope=cdk_app,
        construct_id="TestAppStack",
        app_config=AppConfig(
            app_name="my-app",
            cpu=cpu,
            memory=4096,
            compute_mode="ec2",
            alb_target_container=alb_target_container,
            containers=containers,
            owner_username=None,
            serialize_task_replacement=serialize_task_replacement,
        ),
        image_tags={c.template_path: "test" for c in containers},
        env_slug="staging",
        resource_prefix="humr-staging-my-app",
        subdomain="myapp",
        shared_alb_hosted_zone=None,
        env_bearer_shared_secrets_arn=SHARED_SECRETS_ARN,
        auth_base_url="https://humanityrules.io",
    )
    return Template.from_stack(stack)


class Ec2CpuReservationTests(SimpleTestCase):

    def test_omits_task_cpu_when_all_containers_have_cpu_reservation(self) -> None:
        template = _render_ec2(
            cpu=2048,
            alb_target_container="policy-proxy",
            serialize_task_replacement=True,
            containers=[
                ContainerConfig(
                    name="hermes",
                    image_source=ImageSource.TEMPLATE,
                    template_path="hermes_agent",
                    container_port=8787,
                    memory_limit_mib=4096,
                    memory_reservation_mib=2048,
                    cpu_reservation=1024,
                ),
                ContainerConfig(
                    name="policy-proxy",
                    image_source=ImageSource.TEMPLATE,
                    template_path="policy_proxy",
                    role=ContainerRole.POLICY_PROXY,
                    upstream_container="hermes",
                    container_port=8788,
                    memory_limit_mib=256,
                    cpu_reservation=128,
                ),
            ],
        )

        props = _task_definition_properties(template=template)
        self.assertNotIn("Cpu", props)
        container_defs = {c["Name"]: c for c in props["ContainerDefinitions"]}
        self.assertEqual(container_defs["my-app-hermes"]["Cpu"], 1024)
        self.assertEqual(container_defs["my-app-policy-proxy"]["Cpu"], 128)

    def test_keeps_task_cpu_when_containers_omit_cpu_reservation(self) -> None:
        template = _render_ec2(
            cpu=1024,
            alb_target_container="app",
            serialize_task_replacement=False,
            containers=[
                ContainerConfig(
                    name="app",
                    image_source=ImageSource.TEMPLATE,
                    template_path="app_tree",
                    container_port=8080,
                    memory_limit_mib=512,
                ),
            ],
        )

        props = _task_definition_properties(template=template)
        self.assertEqual(props["Cpu"], "1024")
        self.assertNotIn("Cpu", props["ContainerDefinitions"][0])

    def test_serialized_service_binpacks_by_memory(self) -> None:
        template = _render_ec2(
            cpu=2048,
            alb_target_container="app",
            serialize_task_replacement=True,
            containers=[
                ContainerConfig(
                    name="app",
                    image_source=ImageSource.TEMPLATE,
                    template_path="app_tree",
                    container_port=8080,
                    memory_limit_mib=512,
                ),
            ],
        )

        service = next(r for r in template.to_json()["Resources"].values() if r["Type"] == "AWS::ECS::Service")
        self.assertEqual(service["Properties"]["PlacementStrategies"], [{"Type": "binpack", "Field": "MEMORY"}])

    def test_unserialized_service_spreads_by_az_then_binpacks(self) -> None:
        template = _render_ec2(
            cpu=2048,
            alb_target_container="app",
            serialize_task_replacement=False,
            containers=[
                ContainerConfig(
                    name="app",
                    image_source=ImageSource.TEMPLATE,
                    template_path="app_tree",
                    container_port=8080,
                    memory_limit_mib=512,
                ),
            ],
        )

        service = next(r for r in template.to_json()["Resources"].values() if r["Type"] == "AWS::ECS::Service")
        self.assertEqual(service["Properties"]["PlacementStrategies"], [
            {"Type": "spread", "Field": "attribute:ecs.availability-zone"},
            {"Type": "binpack", "Field": "MEMORY"},
        ])

# Hermes template packing (previously asserted here against hardcoded template
# reservations) is covered by tests/test_node_packing.py: the template now
# carries a fills_node_slot marker resolved at deploy time.
