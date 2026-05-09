"""Tests for Fargate and EC2-backed ECS compute modes."""

from aws_cdk import App
from aws_cdk.assertions import Match, Template
from django.test import SimpleTestCase

from devopshero_app.services.infra_customer import deploy_app
from devopshero_app.services.infra_customer import deploy_base
from devopshero_app.services.infra_customer.appconfig import (
    AppConfig,
    ContainerConfig,
    ContainerDependencyConfig,
    EfsConfig,
    EfsMount,
)


def _app_config(compute_mode: str, include_workspace_efs: bool) -> AppConfig:
    return AppConfig(
        app_name="my-app",
        cpu=1024,
        memory=2048,
        containers=[
            ContainerConfig(
                name="app",
                image_source="dockerfile",
                ecr_repo_name="doh/staging/my-app-app",
                source_repo_path="app",
                container_port=8080,
                health_check_path="/health",
                efs_mounts=["workspace"] if include_workspace_efs else [],
            ),
        ],
        compute_mode=compute_mode,
        alb_target_container="app",
        efs_config=EfsConfig(
            mounts=[
                EfsMount(
                    name="workspace",
                    subpath="workspace",
                    container_path="/workspace",
                    posix_uid=1000,
                    posix_gid=1000,
                ),
            ],
        ) if include_workspace_efs else None,
    )


def _dind_hermes_stack_template() -> Template:
    cdk_app = App()
    stack = deploy_app.AppStack(
        scope=cdk_app,
        construct_id="TestAppStack",
        app_config=AppConfig(
            app_name="my-app",
            cpu=2048,
            memory=4096,
            compute_mode="ec2",
            alb_target_container="hermes",
            efs_config=EfsConfig(
                mounts=[
                    EfsMount(
                        name="home",
                        subpath="hermes",
                        container_path="/home/app/.hermes",
                        posix_uid=1000,
                        posix_gid=1000,
                    ),
                    EfsMount(
                        name="workspace",
                        subpath="workspace",
                        container_path="/workspace",
                        posix_uid=1000,
                        posix_gid=1000,
                    ),
                ],
            ),
            containers=[
                ContainerConfig(
                    name="docker-dind",
                    image_source="registry",
                    registry_image="docker:26.1.0-dind",
                    container_port=0,
                    privileged=True,
                    efs_mounts=["workspace"],
                    command=[
                        "dockerd",
                        "--host=tcp://127.0.0.1:2375",
                    ],
                ),
                ContainerConfig(
                    name="hermes",
                    image_source="dockerfile",
                    source_repo_path="hermes_docker_agent",
                    ecr_repo_name="doh/staging/my-app-hermes",
                    container_port=8787,
                    efs_mounts=["home", "workspace"],
                    depends_on=[ContainerDependencyConfig(
                        name="docker-dind",
                        condition="HEALTHY",
                    )],
                ),
            ],
        ),
        image_tag="test",
        env_slug="staging",
        resource_prefix="doh-staging-my-app",
        subdomain="my-app",
        database_connection_secret=None,
        shared_alb_hosted_zone=None,
        shared_hosted_zone_id=None,
        env_bearer_shared_secrets_arn=None,
        auth_base_url=None,
    )
    return Template.from_stack(stack)


def _app_stack_template(compute_mode: str, include_workspace_efs: bool) -> Template:
    cdk_app = App()
    stack = deploy_app.AppStack(
        scope=cdk_app,
        construct_id="TestAppStack",
        app_config=_app_config(compute_mode=compute_mode, include_workspace_efs=include_workspace_efs),
        image_tag="test",
        env_slug="staging",
        resource_prefix="doh-staging-my-app",
        subdomain="my-app",
        database_connection_secret=None,
        shared_alb_hosted_zone=None,
        shared_hosted_zone_id=None,
        env_bearer_shared_secrets_arn=None,
        auth_base_url=None,
    )
    return Template.from_stack(stack)


class EcsComputeModeTests(SimpleTestCase):

    def test_fargate_mode_uses_fargate_service_and_task_definition(self) -> None:
        template = _app_stack_template(compute_mode="fargate", include_workspace_efs=False)

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
        template = _app_stack_template(compute_mode="ec2", include_workspace_efs=False)

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

    def test_fargate_mode_rejects_privileged(self) -> None:
        cdk_app = App()
        with self.assertRaisesMessage(
            ValueError, "Privileged containers are only supported for EC2-backed ECS tasks",
        ):
            deploy_app.AppStack(
                scope=cdk_app,
                construct_id="TestAppStack",
                app_config=AppConfig(
                    app_name="p",
                    cpu=1024,
                    memory=2048,
                    compute_mode="fargate",
                    alb_target_container="x",
                    containers=[
                        ContainerConfig(
                            name="x",
                            image_source="dockerfile",
                            ecr_repo_name="doh/s/x",
                            source_repo_path="a",
                            container_port=80,
                            privileged=True,
                        ),
                    ],
                ),
                image_tag="t",
                env_slug="staging",
                resource_prefix="doh-s-p",
                subdomain="p",
                database_connection_secret=None,
                shared_alb_hosted_zone=None,
                shared_hosted_zone_id=None,
                env_bearer_shared_secrets_arn=None,
                auth_base_url=None,
            )

    def test_ec2_mode_dind_privileged_and_workspace_only_efs(self) -> None:
        template = _dind_hermes_stack_template()
        template.has_resource_properties("AWS::ECS::TaskDefinition", {
            "ContainerDefinitions": Match.array_with([
                Match.object_like({
                    "Name": "my-app-docker-dind",
                    "Command": [
                        "dockerd",
                        "--host=tcp://127.0.0.1:2375",
                    ],
                    "Privileged": True,
                    "MountPoints": [
                        {
                            "ContainerPath": "/workspace",
                            "SourceVolume": "app-efs-workspace",
                            "ReadOnly": False,
                        },
                    ],
                }),
            ]),
        })
        template.has_resource_properties("AWS::ECS::TaskDefinition", {
            "ContainerDefinitions": Match.array_with([
                Match.object_like({
                    "Name": "my-app-hermes",
                    "DependsOn": [
                        {
                            "ContainerName": "my-app-docker-dind",
                            "Condition": "HEALTHY",
                        },
                    ],
                    "MountPoints": Match.array_with([
                        {
                            "ContainerPath": "/home/app/.hermes",
                            "SourceVolume": "app-efs-home",
                            "ReadOnly": False,
                        },
                        {
                            "ContainerPath": "/workspace",
                            "SourceVolume": "app-efs-workspace",
                            "ReadOnly": False,
                        },
                    ]),
                }),
            ]),
        })

    def test_unknown_container_dependency_gets_clear_error(self) -> None:
        cdk_app = App()
        with self.assertRaisesMessage(
            ValueError, "Container 'app' depends_on unknown container 'missing'",
        ):
            deploy_app.AppStack(
                scope=cdk_app,
                construct_id="TestAppStack",
                app_config=AppConfig(
                    app_name="p",
                    cpu=1024,
                    memory=2048,
                    compute_mode="ec2",
                    alb_target_container="app",
                    containers=[
                        ContainerConfig(
                            name="app",
                            image_source="dockerfile",
                            ecr_repo_name="doh/s/app",
                            source_repo_path="app",
                            container_port=80,
                            depends_on=[ContainerDependencyConfig(
                                name="missing",
                                condition="START",
                            )],
                        ),
                    ],
                ),
                image_tag="t",
                env_slug="staging",
                resource_prefix="doh-s-p",
                subdomain="p",
                database_connection_secret=None,
                shared_alb_hosted_zone=None,
                shared_hosted_zone_id=None,
                env_bearer_shared_secrets_arn=None,
                auth_base_url=None,
            )

    def test_efs_mount_drives_volume_and_mount_point(self) -> None:
        template = _app_stack_template(
            compute_mode="ec2",
            include_workspace_efs=True,
        )

        template.has_resource_properties("AWS::ECS::TaskDefinition", {
            "Volumes": Match.array_with([
                {
                    "Name": "app-efs-workspace",
                    "EFSVolumeConfiguration": Match.object_like({
                        "TransitEncryption": "ENABLED",
                    }),
                },
            ]),
            "ContainerDefinitions": Match.array_with([
                Match.object_like({
                    "MountPoints": Match.array_with([
                        {
                            "ContainerPath": "/workspace",
                            "SourceVolume": "app-efs-workspace",
                            "ReadOnly": False,
                        },
                    ]),
                }),
            ]),
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

        launch_templates = template.find_resources("AWS::EC2::LaunchTemplate")
        launch_template = next(iter(launch_templates.values()))
        user_data_parts = launch_template["Properties"]["LaunchTemplateData"]["UserData"]["Fn::Base64"]["Fn::Join"][1]
        user_data = "".join(part if isinstance(part, str) else "<token>" for part in user_data_parts)
        self.assertIn("dnf install -y kernel6.18", user_data)
        self.assertIn("ECS_CLUSTER=devopshero-staging-cluster", user_data)
        self.assertIn("touch \"$KERNEL_MARKER\"\n  reboot", user_data)
        self.assertNotIn("systemctl stop ecs", user_data)
        self.assertNotIn("systemctl disable ecs", user_data)
        self.assertNotIn("devopshero-start-ecs-after-kernel.service", user_data)
        self.assertLess(user_data.index("ECS_CLUSTER=devopshero-staging-cluster"), user_data.index("dnf install -y kernel6.18"))
        self.assertLess(user_data.index("dnf install -y kernel6.18"), user_data.index("reboot"))
