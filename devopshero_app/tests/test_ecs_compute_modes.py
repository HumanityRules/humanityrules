"""Tests for Fargate and EC2-backed ECS compute modes."""

from aws_cdk import App
from aws_cdk.assertions import Match, Template
from django.test import SimpleTestCase

from devopshero_app.services.infra_customer import deploy_app
from devopshero_app.services.infra_customer import deploy_base
from devopshero_app.services.infra_customer.appconfig import AppConfig, ContainerConfig


def _app_config(compute_mode: str) -> AppConfig:
    return AppConfig(
        app_name="my-app",
        cpu=1024,
        memory=2048,
        containers=[
            ContainerConfig(
                name="app",
                image_source="dockerfile",
                ecr_repo_name="doh/staging/my-app-app",
                container_port=8080,
                health_check_path="/health",
            ),
        ],
        compute_mode=compute_mode,
        alb_target_container="app",
    )


def _app_stack_template(compute_mode: str) -> Template:
    cdk_app = App()
    stack = deploy_app.AppStack(
        scope=cdk_app,
        construct_id="TestAppStack",
        app_config=_app_config(compute_mode=compute_mode),
        image_tag="test",
        env_slug="staging",
        resource_prefix="doh-staging-my-app",
        subdomain="my-app",
        database_connection_secret=None,
        shared_alb_hosted_zone=None,
        shared_hosted_zone_id=None,
        sidecar_shared_secrets_arn=None,
        sidecar_image_version=None,
        auth_base_url=None,
    )
    return Template.from_stack(stack)


class EcsComputeModeTests(SimpleTestCase):

    def test_fargate_mode_uses_fargate_service_and_task_definition(self) -> None:
        template = _app_stack_template(compute_mode="fargate")

        template.has_resource_properties("AWS::ECS::TaskDefinition", {
            "RequiresCompatibilities": ["FARGATE"],
            "Cpu": "1024",
            "Memory": "2048",
            "RuntimePlatform": {
                "CpuArchitecture": "ARM64",
                "OperatingSystemFamily": "LINUX",
            },
        })
        template.has_resource_properties("AWS::ECS::Service", {
            "LaunchType": "FARGATE",
            "CapacityProviderStrategy": Match.absent(),
        })

    def test_ec2_mode_uses_ec2_capacity_provider_and_awsvpc_task(self) -> None:
        template = _app_stack_template(compute_mode="ec2")

        template.has_resource_properties("AWS::ECS::TaskDefinition", {
            "RequiresCompatibilities": ["EC2"],
            "NetworkMode": "awsvpc",
            "Cpu": "1024",
            "Memory": "2048",
        })
        template.has_resource_properties("AWS::ECS::Service", {
            "LaunchType": Match.absent(),
            "CapacityProviderStrategy": [
                {
                    "CapacityProvider": "devopshero-staging-ec2-capacity",
                    "Weight": 1,
                },
            ],
        })

    def test_base_cluster_includes_ec2_capacity_provider(self) -> None:
        cdk_app = App()
        vpc_stack = deploy_base.VpcStack(
            cdk_app,
            "VpcStack",
            env_slug="staging",
            vpc_cidr="10.0.0.0/16",
        )
        cluster_stack = deploy_base.EcsClusterStack(
            cdk_app,
            "ClusterStack",
            env_slug="staging",
            vpc=vpc_stack.vpc,
            shared_hosted_zone_name=None,
            shared_hosted_zone_id=None,
            existing_certificate_arn=None,
        )
        cluster_stack.add_dependency(vpc_stack)
        template = Template.from_stack(cluster_stack)

        template.has_resource_properties("AWS::ECS::CapacityProvider", {
            "Name": "devopshero-staging-ec2-capacity",
            "AutoScalingGroupProvider": {
                "ManagedScaling": {"Status": "ENABLED"},
                "ManagedTerminationProtection": "DISABLED",
            },
        })
        template.has_resource_properties("AWS::AutoScaling::AutoScalingGroup", {
            "MinSize": "0",
            "DesiredCapacity": Match.absent(),
            "MaxSize": "4",
            "LaunchTemplate": {
                "LaunchTemplateId": Match.any_value(),
                "Version": Match.any_value(),
            },
        })
        template.resource_count_is("AWS::AutoScaling::LaunchConfiguration", 0)
        template.resource_count_is("AWS::EC2::LaunchTemplate", 1)
