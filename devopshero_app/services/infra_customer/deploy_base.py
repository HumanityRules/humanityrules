"""
Deploy shared DevOpsHero infrastructure (VPC, ECS cluster) using AWS CDK.
"""

from dataclasses import dataclass
from typing import Callable

import boto3
from aws_cdk import App, Aws, CfnOutput, Fn, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from . import cdk_utils
from . import cloudformation_utils
from . import vpc_utils


# Type alias for log callback: (phase, level, message) -> None
LogCallback = Callable[[str, str, str], None] | None


def _log(phase: str, level: str, message: str, log_callback: LogCallback) -> None:
    """Log a message and optionally call the callback."""
    print(message)
    if log_callback:
        log_callback(phase, level, message)


# =============================================================================
# ENVIRONMENT INFRASTRUCTURE IMPORT
# =============================================================================


@dataclass
class EnvironmentInfrastructure:
    """Imported infrastructure from an environment's base stacks."""

    vpc: ec2.IVpc
    default_security_group: ec2.ISecurityGroup
    cluster: ecs.ICluster
    task_execution_role: iam.IRole
    log_group: logs.ILogGroup


def import_environment_infrastructure(scope: Construct, env_slug: str) -> EnvironmentInfrastructure:
    """
    Import VPC, security group, cluster, and other base infrastructure from an environment.

    Must be called from within a Stack context since it creates CDK constructs.

    Args:
        scope: The CDK construct scope (typically 'self' from within a Stack).
        env_slug: Environment slug (e.g., "default", "prod").

    Returns:
        EnvironmentInfrastructure with all imported resources.
    """
    prefix = f"devopshero-{env_slug}"

    # Import VPC with full subnet configuration
    public_rt = Fn.import_value(f"{prefix}-public-rt")
    private_rt = Fn.import_value(f"{prefix}-private-rt")
    vpc = ec2.Vpc.from_vpc_attributes(
        scope, "ImportedVpc",
        vpc_id=Fn.import_value(f"{prefix}-vpc-id"),
        availability_zones=[Fn.import_value(f"{prefix}-az-1"), Fn.import_value(f"{prefix}-az-2")],
        public_subnet_ids=[Fn.import_value(f"{prefix}-public-subnet-1"), Fn.import_value(f"{prefix}-public-subnet-2")],
        public_subnet_route_table_ids=[public_rt, public_rt],
        private_subnet_ids=[Fn.import_value(f"{prefix}-private-subnet-1"), Fn.import_value(f"{prefix}-private-subnet-2")],
        private_subnet_route_table_ids=[private_rt, private_rt],
    )

    default_security_group = ec2.SecurityGroup.from_security_group_id(
        scope, "ImportedDefaultSg",
        Fn.import_value(f"{prefix}-default-sg-id"),
    )

    # Use literal string for cluster_name so .cluster_name returns actual value, not a CDK token.
    # VPC/subnet IDs must use Fn.import_value (AWS-generated), but cluster name is deterministic.
    cluster = ecs.Cluster.from_cluster_attributes(
        scope, "ImportedCluster",
        cluster_name=f"{prefix}-cluster",
        vpc=vpc,
        security_groups=[],
    )

    task_execution_role = iam.Role.from_role_arn(
        scope, "ImportedTaskExecutionRole",
        Fn.import_value(f"{prefix}-task-execution-role-arn"),
        mutable=False,
    )

    log_group = logs.LogGroup.from_log_group_name(
        scope, "ImportedLogGroup",
        Fn.import_value(f"{prefix}-ecs-log-group"),
    )

    return EnvironmentInfrastructure(
        vpc=vpc,
        default_security_group=default_security_group,
        cluster=cluster,
        task_execution_role=task_execution_role,
        log_group=log_group,
    )


# =============================================================================
# CDK STACKS
# =============================================================================


class VpcStack(Stack):
    """
    DevOpsHero VPC Stack - Public subnets for NAT Gateway, private subnets for Fargate tasks.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_slug: str,
        vpc_cidr: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create VPC with public and private subnets
        # We specify explicit AZs to avoid CDK using dummy values during cross-account synthesis
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            vpc_name=f"devopshero-{env_slug}-vpc",
            ip_addresses=ec2.IpAddresses.cidr(vpc_cidr),
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
            ],
        )

        self.default_security_group = ec2.SecurityGroup(
            self, "DefaultSecurityGroup",
            vpc=self.vpc,
            security_group_name=f"devopshero-{env_slug}-default-sg",
            description="Default security group - allows VPC inbound and all outbound",
            allow_all_outbound=True,
        )
        self.default_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc_cidr), connection=ec2.Port.all_traffic(), description="Allow all traffic from within VPC",
        )

        # Export names include env_slug for environment isolation
        prefix = f"devopshero-{env_slug}"
        CfnOutput(self, "VpcId", value=self.vpc.vpc_id, export_name=f"{prefix}-vpc-id")
        CfnOutput(self, "VpcCidr", value=self.vpc.vpc_cidr_block, export_name=f"{prefix}-vpc-cidr")
        CfnOutput(self, "PublicSubnet1Id", value=self.vpc.public_subnets[0].subnet_id, export_name=f"{prefix}-public-subnet-1")
        CfnOutput(self, "PublicSubnet2Id", value=self.vpc.public_subnets[1].subnet_id, export_name=f"{prefix}-public-subnet-2")
        CfnOutput(self, "PrivateSubnet1Id", value=self.vpc.private_subnets[0].subnet_id, export_name=f"{prefix}-private-subnet-1")
        CfnOutput(self, "PrivateSubnet2Id", value=self.vpc.private_subnets[1].subnet_id, export_name=f"{prefix}-private-subnet-2")
        CfnOutput(self, "DefaultSecurityGroupId", value=self.default_security_group.security_group_id, export_name=f"{prefix}-default-sg-id")
        # Route tables: with nat_gateways=1, all public subnets share one RT, all private subnets share one RT
        CfnOutput(self, "PublicRouteTableId", value=self.vpc.public_subnets[0].route_table.route_table_id, export_name=f"{prefix}-public-rt")
        CfnOutput(self, "PrivateRouteTableId", value=self.vpc.private_subnets[0].route_table.route_table_id, export_name=f"{prefix}-private-rt")
        # Availability zones (needed for cross-stack VPC references)
        CfnOutput(self, "AvailabilityZone1", value=self.vpc.availability_zones[0], export_name=f"{prefix}-az-1")
        CfnOutput(self, "AvailabilityZone2", value=self.vpc.availability_zones[1], export_name=f"{prefix}-az-2")


class EcsClusterStack(Stack):
    """
    DevOpsHero ECS Cluster Stack - Fargate cluster with IAM roles for app deployments.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_slug: str,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.cluster = ecs.Cluster(
            self, "EcsCluster",
            cluster_name=f"devopshero-{env_slug}-cluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        self.task_execution_role = iam.Role(
            self, "TaskExecutionRole",
            role_name=f"devopshero-{env_slug}-task-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")],
        )
        # Allow ECS to inject secrets as env vars (e.g., Aurora credentials)
        self.task_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{Aws.REGION}:{Aws.ACCOUNT_ID}:secret:devopshero/*"],
        ))

        self.log_group = logs.LogGroup(
            self, "EcsLogGroup",
            log_group_name=f"/devopshero/{env_slug}/ecs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Export names include env_slug for environment isolation
        prefix = f"devopshero-{env_slug}"
        CfnOutput(self, "ClusterArn", value=self.cluster.cluster_arn, export_name=f"{prefix}-cluster-arn")
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name, export_name=f"{prefix}-cluster-name")
        CfnOutput(self, "TaskExecutionRoleArn", value=self.task_execution_role.role_arn, export_name=f"{prefix}-task-execution-role-arn")
        CfnOutput(self, "LogGroupName", value=self.log_group.log_group_name, export_name=f"{prefix}-ecs-log-group")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_or_create_vpc_cidr(session: boto3.Session, env_slug: str, log_callback: LogCallback) -> str:
    """Get existing VPC CIDR or find an available one."""
    cf_client = session.client("cloudformation")

    vpc_stack_name = f"devopshero-{env_slug}-vpc"
    if cloudformation_utils.stack_exists(cf_client, vpc_stack_name):
        _log("deploy", "info", f"VPC stack '{vpc_stack_name}' already exists, getting existing CIDR...", log_callback)
        vpc_cidr = cloudformation_utils.get_stack_output(cf_client, vpc_stack_name, "VpcCidr")
        if not vpc_cidr:
            raise RuntimeError("Could not get VPC CIDR from existing stack")
        _log("deploy", "info", f"Using existing VPC CIDR: {vpc_cidr}", log_callback)
        return vpc_cidr
    else:
        _log("deploy", "info", f"VPC stack '{vpc_stack_name}' does not exist, finding available CIDR...", log_callback)
        ec2_client = session.client("ec2")
        vpc_cidr = vpc_utils.find_available_vpc_cidr(ec2_client)["VpcCidr"]
        _log("deploy", "info", f"Found available VPC CIDR: {vpc_cidr}", log_callback)
        return vpc_cidr


# =============================================================================
# DEPLOYMENT FUNCTIONS
# =============================================================================


def deploy(
    session: boto3.Session,
    env_slug: str,
    synth_only: bool,
    log_callback: LogCallback,
) -> bool:
    """
    Deploy shared infrastructure: VPC and ECS cluster.

    Args:
        session: Boto3 session with assumed role credentials.
        env_slug: Environment slug for resource naming (e.g., "default", "prod").
        synth_only: If True, only synthesize templates, don't deploy.
        log_callback: Optional callback for logging progress.

    Returns:
        True on success, False on failure.
    """
    _log("deploy", "info", f"Deploying shared infrastructure for environment '{env_slug}'", log_callback)

    vpc_cidr = get_or_create_vpc_cidr(session, env_slug, log_callback)

    cdk_app = App(outdir=str(cdk_utils.CDK_OUT_DIR))

    vpc_stack_name = f"devopshero-{env_slug}-vpc"
    cluster_stack_name = f"devopshero-{env_slug}-cluster"

    vpc_stack = VpcStack(cdk_app, vpc_stack_name, env_slug=env_slug, vpc_cidr=vpc_cidr)
    ecs_cluster_stack = EcsClusterStack(cdk_app, cluster_stack_name, env_slug=env_slug, vpc=vpc_stack.vpc)
    ecs_cluster_stack.add_dependency(vpc_stack)

    if synth_only:
        cloud_assembly = cdk_app.synth()
        _log("deploy", "info", f"CDK templates synthesized to: {cloud_assembly.directory}", log_callback)
        return True

    success = cdk_utils.deploy_cdk_stacks(cdk_app, session)

    if success:
        _log("deploy", "info", f"Infrastructure deployment complete for environment '{env_slug}'", log_callback)

    return success


def teardown(session: boto3.Session, env_slug: str) -> bool:
    """
    Delete shared infrastructure stacks (ECS cluster, VPC).
    WARNING: This will fail if any app stacks still depend on them.
    """
    cf_client = session.client("cloudformation")

    stacks_to_delete = [
        f"devopshero-{env_slug}-cluster",
        f"devopshero-{env_slug}-vpc",
    ]

    print(f"\n{'='*60}")
    print(f"Tearing down shared infrastructure for environment '{env_slug}'")
    print(f"{'='*60}")
    print(f"\nStacks to delete (in order):")
    for stack in stacks_to_delete:
        print(f"   - {stack}")
    print()

    all_success = True
    for stack_name in stacks_to_delete:
        success = cloudformation_utils.delete_stack_and_wait(cf_client, stack_name=stack_name)
        if not success:
            all_success = False

    if all_success:
        print(f"\n{'='*60}")
        print("Infrastructure stacks deleted successfully")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print("Some stacks failed to delete (apps may still depend on them)")
        print(f"{'='*60}")

    return all_success
