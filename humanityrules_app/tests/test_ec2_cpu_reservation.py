"""Tests for EC2 container-level CPU reservation in deploy_app.AppStack."""

from aws_cdk import App
from aws_cdk.assertions import Template
from django.test import SimpleTestCase

from humanityrules_app.management.commands import seed_app_templates
from humanityrules_app.services.infra_customer import deploy_app
from humanityrules_app.services.infra_customer.appconfig import AppConfig, ContainerConfig


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
        image_tag="test",
        env_slug="staging",
        resource_prefix="humr-staging-my-app",
        subdomain="myapp",
        database_connection_secret=None,
        shared_alb_hosted_zone=None,
        shared_hosted_zone_id=None,
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
                    image_source="dockerfile",
                    ecr_repo_name="humr/staging/my-app-hermes",
                    container_port=8787,
                    memory_limit_mib=4096,
                    memory_reservation_mib=2048,
                    cpu_reservation=1024,
                ),
                ContainerConfig(
                    name="policy-proxy",
                    image_source="policy_proxy",
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
                    image_source="dockerfile",
                    ecr_repo_name="humr/staging/my-app-app",
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
                    image_source="dockerfile",
                    ecr_repo_name="humr/staging/my-app-app",
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
                    image_source="dockerfile",
                    ecr_repo_name="humr/staging/my-app-app",
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

    def test_hermes_template_packs_two_tasks_per_t4g_large(self) -> None:
        # Two tasks per node is the awsvpc ENI ceiling on a .large (3 ENIs,
        # one for the host), so each task claims half the node.
        template = seed_app_templates.HERMES_PERSONAL_TEMPLATE

        hermes = next(c for c in template["containers"] if c["name"] == "hermes")
        proxy = next(c for c in template["containers"] if c["name"] == "policy-proxy")
        self.assertEqual(hermes["cpu_reservation"], 896)
        self.assertEqual(proxy["cpu_reservation"], 128)
        # ECS sums every container's reservation for placement; the per-task
        # total must be <= 1024 so two tasks fit on one t4g.large (2048 units).
        per_task_cpu = hermes["cpu_reservation"] + proxy["cpu_reservation"]
        self.assertEqual(per_task_cpu, 1024)
        self.assertLessEqual(per_task_cpu * 2, 2048)
        # Memory: 2 x (3328 + 256) = 7168 MiB fits in a t4g.large's ~7600 MiB
        # usable.
        per_task_mem = hermes["memory_reservation_mib"] + proxy["memory_limit_mib"]
        self.assertLessEqual(per_task_mem * 2, 7600)
