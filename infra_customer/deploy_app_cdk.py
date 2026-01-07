#!/usr/bin/env python3
"""
Deploy DevOpsHero infrastructure and apps using AWS CDK.

This is a CDK equivalent of deploy_app_cf.py, using Python CDK constructs instead
of CloudFormation JSON templates.

This module provides deployment functions for CDK-based deployments.
Use deploy_app_simple_dashboard.py as the entry point.
"""

import os
import subprocess
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from appconfig import AppConfig

# CDK output directory (inside infra_customer/)
CDK_OUT_DIR = Path(__file__).parent / "cdk.out"

# AWS CDK imports
from aws_cdk import (
    App,
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from constructs import Construct

import cloudformation_utils
import ecr_utils
import ecs_service_stable
import route53_utils
import vpc_utils


# =============================================================================
# CDK STACKS
# =============================================================================


class VpcStack(Stack):
    """
    DevOpsHero VPC Stack - Public subnets for NAT Gateway, private subnets for Fargate tasks.

    Equivalent to cf_vpc.json
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc_cidr: str,
        availability_zones: list[str],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create VPC with public and private subnets
        # We specify explicit AZs to avoid CDK using dummy values during cross-account synthesis
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            vpc_name="devopshero-vpc",
            ip_addresses=ec2.IpAddresses.cidr(vpc_cidr),
            availability_zones=availability_zones,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        # Default security group - allows VPC inbound and all outbound
        self.default_security_group = ec2.SecurityGroup(
            self,
            "DefaultSecurityGroup",
            vpc=self.vpc,
            security_group_name="devopshero-default-sg",
            description="Default security group - allows VPC inbound and all outbound",
            allow_all_outbound=True,
        )
        self.default_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc_cidr),
            connection=ec2.Port.all_traffic(),
            description="Allow all traffic from within VPC",
        )

        # Outputs (exported for cross-stack references)
        CfnOutput(
            self,
            "VpcId",
            value=self.vpc.vpc_id,
            export_name="devopshero-vpc-id",
            description="VPC ID",
        )
        CfnOutput(
            self,
            "VpcCidr",
            value=self.vpc.vpc_cidr_block,
            export_name="devopshero-vpc-cidr",
            description="VPC CIDR block",
        )
        CfnOutput(
            self,
            "PublicSubnet1Id",
            value=self.vpc.public_subnets[0].subnet_id,
            export_name="devopshero-public-subnet-1",
            description="Public Subnet 1 ID",
        )
        CfnOutput(
            self,
            "PublicSubnet2Id",
            value=self.vpc.public_subnets[1].subnet_id,
            export_name="devopshero-public-subnet-2",
            description="Public Subnet 2 ID",
        )
        CfnOutput(
            self,
            "PrivateSubnet1Id",
            value=self.vpc.private_subnets[0].subnet_id,
            export_name="devopshero-private-subnet-1",
            description="Private Subnet 1 ID (for Fargate tasks)",
        )
        CfnOutput(
            self,
            "PrivateSubnet2Id",
            value=self.vpc.private_subnets[1].subnet_id,
            export_name="devopshero-private-subnet-2",
            description="Private Subnet 2 ID (for Fargate tasks)",
        )
        CfnOutput(
            self,
            "DefaultSecurityGroupId",
            value=self.default_security_group.security_group_id,
            export_name="devopshero-default-sg-id",
            description="Default Security Group ID",
        )


class EcsClusterStack(Stack):
    """
    DevOpsHero ECS Cluster Stack - Fargate cluster with IAM roles for app deployments.

    Equivalent to cf_ecs_cluster.json
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ECS Cluster with Container Insights (using the provided VPC)
        self.cluster = ecs.Cluster(
            self,
            "EcsCluster",
            cluster_name="devopshero-cluster",
            vpc=vpc,
            container_insights=True,
        )

        # Task Execution Role (for ECS to pull images and write logs)
        self.task_execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            role_name="devopshero-ecs-task-execution-role",
            description="Role for ECS to pull images from ECR and write logs to CloudWatch",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
            ],
        )

        # Task Role (for applications to access AWS services)
        self.task_role = iam.Role(
            self,
            "TaskRole",
            role_name="devopshero-ecs-task-role",
            description="Role for applications running in ECS tasks to access AWS services",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # CloudWatch Log Group
        self.log_group = logs.LogGroup(
            self,
            "EcsLogGroup",
            log_group_name="/devopshero/ecs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Outputs
        CfnOutput(
            self,
            "ClusterArn",
            value=self.cluster.cluster_arn,
            export_name="devopshero-cluster-arn",
            description="ECS Cluster ARN",
        )
        CfnOutput(
            self,
            "ClusterName",
            value=self.cluster.cluster_name,
            export_name="devopshero-cluster-name",
            description="ECS Cluster Name",
        )
        CfnOutput(
            self,
            "TaskExecutionRoleArn",
            value=self.task_execution_role.role_arn,
            export_name="devopshero-task-execution-role-arn",
            description="ECS Task Execution Role ARN",
        )
        CfnOutput(
            self,
            "TaskRoleArn",
            value=self.task_role.role_arn,
            export_name="devopshero-task-role-arn",
            description="ECS Task Role ARN",
        )
        CfnOutput(
            self,
            "LogGroupName",
            value=self.log_group.log_group_name,
            export_name="devopshero-ecs-log-group",
            description="CloudWatch Log Group for ECS tasks",
        )


class EcrStack(Stack):
    """
    DevOpsHero ECR Stack - Container Registry for the app.
    
    Equivalent to cf_ecr.json
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        app_config: AppConfig,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ECR Repository
        self.repository = ecr.Repository(
            self,
            "EcrRepository",
            repository_name=app_config.ecr_repo_name,
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 10 images",
                    max_image_count=10,
                    rule_priority=1,
                ),
            ],
        )
        Tags.of(self.repository).add("App", app_config.app_name)

        # Outputs
        CfnOutput(
            self,
            "EcrRepositoryUri",
            value=self.repository.repository_uri,
            export_name=f"devopshero-{app_config.app_name}-ecr-uri",
            description="ECR Repository URI",
        )
        CfnOutput(
            self,
            "EcrRepositoryArn",
            value=self.repository.repository_arn,
            export_name=f"devopshero-{app_config.app_name}-ecr-arn",
            description="ECR Repository ARN",
        )


class AppWithAlbStack(Stack):
    """
    DevOpsHero App Stack - ECS Service with ALB.
    
    Equivalent to cf_app_with_alb.json
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        cluster: ecs.ICluster,
        task_execution_role: iam.IRole,
        task_role: iam.IRole,
        log_group: logs.ILogGroup,
        default_security_group: ec2.ISecurityGroup,
        app_config: AppConfig,
        image_tag: str,
        hosted_zone_id: str | None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Build environment variables for the container
        environment = {
            env["name"]: env["value"] for env in app_config.environment_variables
        }

        # Task Definition
        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            family=f"devopshero-{app_config.app_name}",
            cpu=app_config.cpu,
            memory_limit_mib=app_config.memory,
            execution_role=task_execution_role,
            task_role=task_role,
        )

        # Container
        container = task_definition.add_container(
            "AppContainer",
            container_name=app_config.app_name,
            image=ecs.ContainerImage.from_registry(
                f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/{app_config.ecr_repo_name}:{image_tag}"
            ),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix=app_config.app_name,
                log_group=log_group,
            ),
            environment=environment,
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    app_config.health_check_command or f"curl -f http://localhost:{app_config.container_port}{app_config.health_check_path} || exit 1",
                ],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(60),
            ) if app_config.health_check_command else None,
        )
        container.add_port_mappings(
            ecs.PortMapping(
                container_port=app_config.container_port,
                protocol=ecs.Protocol.TCP,
            )
        )

        # ALB Security Group
        alb_security_group = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=vpc,
            security_group_name=f"devopshero-{app_config.app_name}-alb-sg",
            description="Security group for ALB - allows HTTP and HTTPS from internet",
            allow_all_outbound=True,
        )
        alb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
            description="Allow HTTP from anywhere",
        )
        if app_config.domain_name and app_config.hosted_zone_name:
            alb_security_group.add_ingress_rule(
                peer=ec2.Peer.any_ipv4(),
                connection=ec2.Port.tcp(443),
                description="Allow HTTPS from anywhere",
            )

        # Application Load Balancer
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "ApplicationLoadBalancer",
            load_balancer_name=f"devopshero-{app_config.app_name}-alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_security_group,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        # Target Group
        target_group = elbv2.ApplicationTargetGroup(
            self,
            "TargetGroup",
            target_group_name=f"devopshero-{app_config.app_name}-tg",
            vpc=vpc,
            port=app_config.container_port,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                enabled=True,
                path=app_config.health_check_path,
                protocol=elbv2.Protocol.HTTP,
                interval=Duration.seconds(15),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                healthy_http_codes="200",
            ),
        )

        # Certificate and HTTPS (if domain configured)
        certificate = None
        if app_config.domain_name and app_config.hosted_zone_name and hosted_zone_id:
            # Look up the hosted zone
            hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
                self,
                "HostedZone",
                hosted_zone_id=hosted_zone_id,
                zone_name=app_config.hosted_zone_name,
            )

            # ACM Certificate with DNS validation
            certificate = acm.Certificate(
                self,
                "Certificate",
                domain_name=app_config.domain_name,
                validation=acm.CertificateValidation.from_dns(hosted_zone),
            )
            Tags.of(certificate).add("Name", f"devopshero-{app_config.app_name}-cert")

            # HTTPS Listener
            https_listener = alb.add_listener(
                "HttpsListener",
                port=443,
                protocol=elbv2.ApplicationProtocol.HTTPS,
                certificates=[certificate],
                ssl_policy=elbv2.SslPolicy.TLS13_RES,
                default_target_groups=[target_group],
            )

            # HTTP Listener (redirect to HTTPS)
            http_listener = alb.add_listener(
                "HttpListener",
                port=80,
                protocol=elbv2.ApplicationProtocol.HTTP,
                default_action=elbv2.ListenerAction.redirect(
                    protocol="HTTPS",
                    port="443",
                    permanent=True,
                ),
            )

            # DNS Record
            route53.ARecord(
                self,
                "DnsRecord",
                zone=hosted_zone,
                record_name=app_config.domain_name,
                target=route53.RecordTarget.from_alias(
                    targets.LoadBalancerTarget(alb)
                ),
            )

            # HTTPS URL output
            CfnOutput(
                self,
                "HttpsUrl",
                value=f"https://{app_config.domain_name}",
                export_name=f"devopshero-{app_config.app_name}-https-url",
                description="Full HTTPS URL to access the app via custom domain",
            )
            CfnOutput(
                self,
                "CertificateArn",
                value=certificate.certificate_arn,
                export_name=f"devopshero-{app_config.app_name}-cert-arn",
                description="ACM Certificate ARN",
            )
        else:
            # HTTP only listener (no domain configured)
            http_listener = alb.add_listener(
                "HttpListener",
                port=80,
                protocol=elbv2.ApplicationProtocol.HTTP,
                default_target_groups=[target_group],
            )

        # ECS Service
        service = ecs.FargateService(
            self,
            "EcsService",
            service_name=app_config.app_name,
            cluster=cluster,
            task_definition=task_definition,
            desired_count=0,  # Start at 0, will be scaled up after image push
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[default_security_group],
            enable_execute_command=True,
            deployment_controller=ecs.DeploymentController(
                type=ecs.DeploymentControllerType.ECS,
            ),
            min_healthy_percent=100,
            max_healthy_percent=200,
        )

        # Register service with target group
        service.attach_to_application_target_group(target_group)

        # Tags
        Tags.of(service).add("App", app_config.app_name)
        Tags.of(task_definition).add("App", app_config.app_name)

        # Outputs
        CfnOutput(
            self,
            "TaskDefinitionArn",
            value=task_definition.task_definition_arn,
            export_name=f"devopshero-{app_config.app_name}-task-def-arn",
            description="ECS Task Definition ARN",
        )
        CfnOutput(
            self,
            "AlbDnsName",
            value=alb.load_balancer_dns_name,
            export_name=f"devopshero-{app_config.app_name}-alb-dns",
            description="ALB DNS Name - use this to access the app",
        )
        CfnOutput(
            self,
            "AlbUrl",
            value=f"http://{alb.load_balancer_dns_name}",
            export_name=f"devopshero-{app_config.app_name}-alb-url",
            description="Full URL to access the app via ALB",
        )
        CfnOutput(
            self,
            "AlbArn",
            value=alb.load_balancer_arn,
            export_name=f"devopshero-{app_config.app_name}-alb-arn",
            description="ALB ARN",
        )
        CfnOutput(
            self,
            "TargetGroupArn",
            value=target_group.target_group_arn,
            export_name=f"devopshero-{app_config.app_name}-tg-arn",
            description="Target Group ARN",
        )
        CfnOutput(
            self,
            "ServiceArn",
            value=service.service_arn,
            export_name=f"devopshero-{app_config.app_name}-service-arn",
            description="ECS Service ARN",
        )
        CfnOutput(
            self,
            "ServiceName",
            value=app_config.app_name,
            export_name=f"devopshero-{app_config.app_name}-service-name",
            description="ECS Service Name",
        )


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def deploy_cdk_stacks(
    app: App,
    session: boto3.Session,
) -> bool:
    """
    Synthesize and deploy CDK stacks using the CDK CLI.
    
    Returns True on success, False on failure.
    """
    # Synthesize the CDK app
    print(f"\n{'='*60}")
    print(f"📦 Synthesizing CDK stacks...")

    # Get credentials from the session
    credentials = session.get_credentials()
    frozen_credentials = credentials.get_frozen_credentials()

    # Set up environment for CDK CLI (region is specified in each stack's env property)
    cdk_env = os.environ.copy()
    cdk_env["AWS_ACCESS_KEY_ID"] = frozen_credentials.access_key
    cdk_env["AWS_SECRET_ACCESS_KEY"] = frozen_credentials.secret_key
    if frozen_credentials.token:
        cdk_env["AWS_SESSION_TOKEN"] = frozen_credentials.token

    # Write the CDK app to cdk.out
    cloud_assembly = app.synth()

    # Deploy using CDK CLI
    print(f"\n{'='*60}")
    print(f"🚀 Deploying CDK stacks...")

    cdk_out_dir = cloud_assembly.directory

    deploy_result = subprocess.run(
        [
            "npx", "cdk", "deploy",
            "--all",
            "--require-approval", "never",
            "--app", cdk_out_dir,
        ],
        env=cdk_env,
        capture_output=False,  # Show output in real-time
    )

    if deploy_result.returncode != 0:
        print(f"\n❌ CDK deployment failed")
        return False

    print(f"\n✅ CDK deployment complete")
    return True


def start_ecs_service(
    session: boto3.Session,
    app_config: AppConfig,
) -> bool:
    """Start the ECS service (set desiredCount to 1) and wait for stabilization."""
    print("\n📦 Starting ECS service (desiredCount=1)...")
    ecs_client = session.client("ecs")

    try:
        ecs_client.update_service(
            cluster="devopshero-cluster",
            service=app_config.app_name,
            desiredCount=1,
            forceNewDeployment=True,
        )
        print("   ✅ Deployment triggered (desiredCount=1)")
    except ClientError as e:
        print(f"   ❌ Failed to trigger deployment: {e}")
        return False

    # Wait for service to stabilize
    stable = ecs_service_stable.wait_for_service_stable(
        ecs_client=ecs_client,
        cluster="devopshero-cluster",
        service=app_config.app_name,
        timeout_seconds=180,
    )

    if not stable:
        print("\n❌ Service failed to stabilize. Check ECS console for details.")
        return False

    return True


def create_app_stack_with_imports(
    cdk_app: App,
    app_config: AppConfig,
    image_tag: str,
    hosted_zone_id: str | None,
    account_id: str,
    region: str,
) -> Stack:
    """Create the app stack that imports VPC and cluster from existing stacks."""

    class AppStackWithImports(Stack):
        """App stack that imports VPC and cluster from existing stacks."""

        def __init__(
            self,
            scope: Construct,
            construct_id: str,
            app_config: AppConfig,
            image_tag: str,
            hosted_zone_id: str | None,
            **kwargs,
        ) -> None:
            super().__init__(scope, construct_id, **kwargs)

            # Import VPC
            vpc = ec2.Vpc.from_vpc_attributes(
                self,
                "ImportedVpc",
                vpc_id=Fn.import_value("devopshero-vpc-id"),
                availability_zones=[f"{region}a", f"{region}b"],
                public_subnet_ids=[
                    Fn.import_value("devopshero-public-subnet-1"),
                    Fn.import_value("devopshero-public-subnet-2"),
                ],
                private_subnet_ids=[
                    Fn.import_value("devopshero-private-subnet-1"),
                    Fn.import_value("devopshero-private-subnet-2"),
                ],
            )

            # Import cluster
            cluster = ecs.Cluster.from_cluster_attributes(
                self,
                "ImportedCluster",
                cluster_name=Fn.import_value("devopshero-cluster-name"),
                vpc=vpc,
                security_groups=[],
            )

            # Import roles
            task_execution_role = iam.Role.from_role_arn(
                self,
                "ImportedTaskExecutionRole",
                role_arn=Fn.import_value("devopshero-task-execution-role-arn"),
            )

            task_role = iam.Role.from_role_arn(
                self,
                "ImportedTaskRole",
                role_arn=Fn.import_value("devopshero-task-role-arn"),
            )

            # Import log group
            log_group = logs.LogGroup.from_log_group_name(
                self,
                "ImportedLogGroup",
                log_group_name=Fn.import_value("devopshero-ecs-log-group"),
            )

            # Import security group
            default_sg = ec2.SecurityGroup.from_security_group_id(
                self,
                "ImportedDefaultSg",
                security_group_id=Fn.import_value("devopshero-default-sg-id"),
            )

            # Build environment variables for the container
            environment = {
                env["name"]: env["value"] for env in app_config.environment_variables
            }

            # Task Definition
            task_definition = ecs.FargateTaskDefinition(
                self,
                "TaskDefinition",
                family=f"devopshero-{app_config.app_name}",
                cpu=app_config.cpu,
                memory_limit_mib=app_config.memory,
                execution_role=task_execution_role,
                task_role=task_role,
            )

            # Container
            container = task_definition.add_container(
                "AppContainer",
                container_name=app_config.app_name,
                image=ecs.ContainerImage.from_registry(
                    f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/{app_config.ecr_repo_name}:{image_tag}"
                ),
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix=app_config.app_name,
                    log_group=log_group,
                ),
                environment=environment,
                health_check=ecs.HealthCheck(
                    command=[
                        "CMD-SHELL",
                        app_config.health_check_command,
                    ],
                    interval=Duration.seconds(30),
                    timeout=Duration.seconds(10),
                    retries=3,
                    start_period=Duration.seconds(60),
                ) if app_config.health_check_command else None,
            )
            container.add_port_mappings(
                ecs.PortMapping(
                    container_port=app_config.container_port,
                    protocol=ecs.Protocol.TCP,
                )
            )

            # ALB Security Group
            alb_security_group = ec2.SecurityGroup(
                self,
                "AlbSecurityGroup",
                vpc=vpc,
                security_group_name=f"devopshero-{app_config.app_name}-alb-sg",
                description="Security group for ALB - allows HTTP and HTTPS from internet",
                allow_all_outbound=True,
            )
            alb_security_group.add_ingress_rule(
                peer=ec2.Peer.any_ipv4(),
                connection=ec2.Port.tcp(80),
                description="Allow HTTP from anywhere",
            )
            if app_config.domain_name and app_config.hosted_zone_name:
                alb_security_group.add_ingress_rule(
                    peer=ec2.Peer.any_ipv4(),
                    connection=ec2.Port.tcp(443),
                    description="Allow HTTPS from anywhere",
                )

            # Application Load Balancer
            alb = elbv2.ApplicationLoadBalancer(
                self,
                "ApplicationLoadBalancer",
                load_balancer_name=f"doh-{app_config.app_name}-alb"[:32],  # ALB name max 32 chars
                vpc=vpc,
                internet_facing=True,
                security_group=alb_security_group,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            )

            # Target Group
            target_group = elbv2.ApplicationTargetGroup(
                self,
                "TargetGroup",
                target_group_name=f"doh-{app_config.app_name}-tg"[:32],  # TG name max 32 chars
                vpc=vpc,
                port=app_config.container_port,
                protocol=elbv2.ApplicationProtocol.HTTP,
                target_type=elbv2.TargetType.IP,
                health_check=elbv2.HealthCheck(
                    enabled=True,
                    path=app_config.health_check_path,
                    protocol=elbv2.Protocol.HTTP,
                    interval=Duration.seconds(15),
                    timeout=Duration.seconds(5),
                    healthy_threshold_count=2,
                    unhealthy_threshold_count=3,
                    healthy_http_codes="200",
                ),
            )

            # Certificate and HTTPS (if domain configured)
            if app_config.domain_name and app_config.hosted_zone_name and hosted_zone_id:
                hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
                    self,
                    "HostedZone",
                    hosted_zone_id=hosted_zone_id,
                    zone_name=app_config.hosted_zone_name,
                )

                certificate = acm.Certificate(
                    self,
                    "Certificate",
                    domain_name=app_config.domain_name,
                    validation=acm.CertificateValidation.from_dns(hosted_zone),
                )
                Tags.of(certificate).add("Name", f"devopshero-{app_config.app_name}-cert")

                # HTTPS Listener
                alb.add_listener(
                    "HttpsListener",
                    port=443,
                    protocol=elbv2.ApplicationProtocol.HTTPS,
                    certificates=[certificate],
                    ssl_policy=elbv2.SslPolicy.TLS13_RES,
                    default_target_groups=[target_group],
                )

                # HTTP Listener (redirect to HTTPS)
                alb.add_listener(
                    "HttpListener",
                    port=80,
                    protocol=elbv2.ApplicationProtocol.HTTP,
                    default_action=elbv2.ListenerAction.redirect(
                        protocol="HTTPS",
                        port="443",
                        permanent=True,
                    ),
                )

                # DNS Record
                route53.ARecord(
                    self,
                    "DnsRecord",
                    zone=hosted_zone,
                    record_name=app_config.domain_name,
                    target=route53.RecordTarget.from_alias(
                        targets.LoadBalancerTarget(alb)
                    ),
                )

                CfnOutput(
                    self,
                    "HttpsUrl",
                    value=f"https://{app_config.domain_name}",
                    export_name=f"devopshero-{app_config.app_name}-https-url",
                    description="Full HTTPS URL to access the app via custom domain",
                )
                CfnOutput(
                    self,
                    "CertificateArn",
                    value=certificate.certificate_arn,
                    export_name=f"devopshero-{app_config.app_name}-cert-arn",
                    description="ACM Certificate ARN",
                )
            else:
                # HTTP only listener
                alb.add_listener(
                    "HttpListener",
                    port=80,
                    protocol=elbv2.ApplicationProtocol.HTTP,
                    default_target_groups=[target_group],
                )

            # ECS Service
            service = ecs.FargateService(
                self,
                "EcsService",
                service_name=app_config.app_name,
                cluster=cluster,
                task_definition=task_definition,
                desired_count=0,
                assign_public_ip=False,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                security_groups=[default_sg],
                enable_execute_command=True,
                min_healthy_percent=100,
                max_healthy_percent=200,
            )

            service.attach_to_application_target_group(target_group)

            # Tags
            Tags.of(service).add("App", app_config.app_name)
            Tags.of(task_definition).add("App", app_config.app_name)

            # Outputs
            CfnOutput(
                self,
                "TaskDefinitionArn",
                value=task_definition.task_definition_arn,
                export_name=f"devopshero-{app_config.app_name}-task-def-arn",
                description="ECS Task Definition ARN",
            )
            CfnOutput(
                self,
                "AlbDnsName",
                value=alb.load_balancer_dns_name,
                export_name=f"devopshero-{app_config.app_name}-alb-dns",
                description="ALB DNS Name",
            )
            CfnOutput(
                self,
                "AlbUrl",
                value=f"http://{alb.load_balancer_dns_name}",
                export_name=f"devopshero-{app_config.app_name}-alb-url",
                description="Full URL to access the app via ALB",
            )
            CfnOutput(
                self,
                "ServiceArn",
                value=service.service_arn,
                export_name=f"devopshero-{app_config.app_name}-service-arn",
                description="ECS Service ARN",
            )
            CfnOutput(
                self,
                "ServiceName",
                value=app_config.app_name,
                export_name=f"devopshero-{app_config.app_name}-service-name",
                description="ECS Service Name",
            )

    return AppStackWithImports(
        cdk_app,
        f"devopshero-app-with-alb-{app_config.app_name}",
        app_config=app_config,
        image_tag=image_tag,
        hosted_zone_id=hosted_zone_id,
        env={"account": account_id, "region": region},
    )


# =============================================================================
# MAIN DEPLOY FUNCTION
# =============================================================================


def deploy(
    session: boto3.Session,
    account_id: str,
    region: str,
    app_config: AppConfig,
    image_tag: str,
    synth_only: bool,
) -> bool:
    """
    Main deployment function for CDK-based deployments.
    
    Args:
        session: Boto3 session with assumed role credentials
        account_id: Target AWS account ID
        region: Target AWS region
        app_config: Application configuration
        image_tag: Docker image tag to deploy
        synth_only: If True, only synthesize templates, don't deploy
    
    Returns:
        True on success, False on failure
    """
    # Look up hosted zone ID if domain is configured
    hosted_zone_id = None
    if app_config.domain_name and app_config.hosted_zone_name:
        print(f"\n🔍 Looking up hosted zone for {app_config.hosted_zone_name}...")
        hosted_zone_id = route53_utils.get_hosted_zone_id(session=session, hosted_zone_name=app_config.hosted_zone_name)
        if hosted_zone_id:
            print(f"   ✅ Found hosted zone: {hosted_zone_id}")
        else:
            print(f"   ⚠️  Could not find hosted zone, HTTPS will not be configured")

    # Create AWS clients for VPC CIDR lookup
    cf_client = session.client("cloudformation")
    ec2_client = session.client("ec2")

    # Check if VPC stack already exists (CIDRs are immutable, can't change them on update)
    vpc_stack_name = "devopshero-vpc-cdk"
    vpc_stack_exists = cloudformation_utils.stack_exists(cf_client, vpc_stack_name)

    if vpc_stack_exists:
        # Stack exists - get existing CIDR from stack outputs
        print(f"\n📦 VPC stack '{vpc_stack_name}' already exists, getting existing CIDR...")
        vpc_cidr = cloudformation_utils.get_stack_output(cf_client, stack_name=vpc_stack_name, output_key="VpcCidr")
        if not vpc_cidr:
            print("   ❌ Could not get VPC CIDR from existing stack")
            return False
        print(f"   ✅ Using existing VPC CIDR: {vpc_cidr}")
    else:
        # New stack - find available CIDR range
        print(f"\n📦 VPC stack '{vpc_stack_name}' does not exist, finding available CIDR...")
        cidr_config = vpc_utils.find_available_vpc_cidr(ec2_client)
        vpc_cidr = cidr_config["VpcCidr"]

    # Availability zones - use first two AZs in the region
    availability_zones = [f"{region}a", f"{region}b"]

    # Create CDK App with explicit output directory
    cdk_app = App(outdir=str(CDK_OUT_DIR))

    # Infrastructure stacks
    vpc_stack = VpcStack(
        cdk_app,
        vpc_stack_name,
        vpc_cidr=vpc_cidr,
        availability_zones=availability_zones,
        env={"account": account_id, "region": region},
    )

    ecs_cluster_stack = EcsClusterStack(
        cdk_app,
        "devopshero-ecs-cluster-cdk",
        vpc=vpc_stack.vpc,
        env={"account": account_id, "region": region},
    )
    ecs_cluster_stack.add_dependency(vpc_stack)

    # App stacks
    ecr_stack = EcrStack(
        cdk_app,
        f"devopshero-ecr-{app_config.app_name}",
        app_config=app_config,
        env={"account": account_id, "region": region},
    )

    app_stack = create_app_stack_with_imports(
        cdk_app=cdk_app,
        app_config=app_config,
        image_tag=image_tag,
        hosted_zone_id=hosted_zone_id,
        account_id=account_id,
        region=region,
    )

    # Set up dependencies
    app_stack.add_dependency(vpc_stack)
    app_stack.add_dependency(ecs_cluster_stack)
    app_stack.add_dependency(ecr_stack)

    # Synth only mode
    if synth_only:
        cloud_assembly = cdk_app.synth()
        print(f"\n✅ CDK templates synthesized to: {cloud_assembly.directory}")
        return True

    # Deploy CDK stacks
    success = deploy_cdk_stacks(
        app=cdk_app,
        session=session,
    )

    if not success:
        return False

    # Build and push Docker image
    print("\n📦 Building and pushing Docker image...")
    image_uri = ecr_utils.build_and_push_docker_image(
        session=session,
        account_id=account_id,
        region=region,
        app_name=app_config.app_name,
        ecr_repo_name=app_config.ecr_repo_name,
        app_source_path=app_config.app_source_path,
        image_tag=image_tag,
    )

    if not image_uri:
        print("\n❌ Docker build/push failed.")
        return False

    # Start ECS service
    success = start_ecs_service(
        session=session,
        app_config=app_config,
    )

    if not success:
        return False

    # Print summary
    print("\n" + "=" * 60)
    print("🎉 CDK Deployment complete!")
    print("=" * 60)
    print(f"\nAccount: {account_id}")
    print(f"Region:  {region}")

    cf_client = session.client("cloudformation")
    print(f"\n📊 App: {app_config.app_name}")
    print(f"   Image tag: {image_tag}")

    # Show app URLs
    urls = cloudformation_utils.get_app_urls(
        cf_client,
        app_name=app_config.app_name,
        has_domain=bool(app_config.domain_name),
    )

    if urls.get("https_url"):
        print(f"\n🔒 App URL (HTTPS): {urls['https_url']}")

    if urls.get("alb_url"):
        print(f"🌐 App URL (ALB):   {urls['alb_url']}")
    else:
        print("\n   URLs: (waiting for ALB to be ready...)")

    print(f"\n   Or use ECS Exec to connect to the container:")
    print(f"   aws ecs execute-command --cluster devopshero-cluster \\")
    print(f"       --task <task-id> --container {app_config.app_name} \\")
    print(f"       --interactive --command /bin/sh")

    return True
