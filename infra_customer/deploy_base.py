"""
Deploy shared DevOpsHero infrastructure (VPC, ECS cluster) using AWS CDK.
"""

import boto3
from aws_cdk import App, Aws, CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

import cdk_utils
import cloudformation_utils
import vpc_utils


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
        vpc_cidr: str,
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
            security_group_name="devopshero-default-sg",
            description="Default security group - allows VPC inbound and all outbound",
            allow_all_outbound=True,
        )
        self.default_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc_cidr), connection=ec2.Port.all_traffic(), description="Allow all traffic from within VPC",
        )

        CfnOutput(self, "VpcId", value=self.vpc.vpc_id, export_name="devopshero-vpc-id")
        CfnOutput(self, "VpcCidr", value=self.vpc.vpc_cidr_block, export_name="devopshero-vpc-cidr")
        CfnOutput(self, "PublicSubnet1Id", value=self.vpc.public_subnets[0].subnet_id, export_name="devopshero-public-subnet-1")
        CfnOutput(self, "PublicSubnet2Id", value=self.vpc.public_subnets[1].subnet_id, export_name="devopshero-public-subnet-2")
        CfnOutput(self, "PrivateSubnet1Id", value=self.vpc.private_subnets[0].subnet_id, export_name="devopshero-private-subnet-1")
        CfnOutput(self, "PrivateSubnet2Id", value=self.vpc.private_subnets[1].subnet_id, export_name="devopshero-private-subnet-2")
        CfnOutput(self, "DefaultSecurityGroupId", value=self.default_security_group.security_group_id, export_name="devopshero-default-sg-id")
        # Route tables: with nat_gateways=1, all public subnets share one RT, all private subnets share one RT
        CfnOutput(self, "PublicRouteTableId", value=self.vpc.public_subnets[0].route_table.route_table_id, export_name="devopshero-public-rt")
        CfnOutput(self, "PrivateRouteTableId", value=self.vpc.private_subnets[0].route_table.route_table_id, export_name="devopshero-private-rt")


class EcsClusterStack(Stack):
    """
    DevOpsHero ECS Cluster Stack - Fargate cluster with IAM roles for app deployments.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.cluster = ecs.Cluster(self, "EcsCluster", cluster_name="devopshero-cluster", vpc=vpc, container_insights_v2=ecs.ContainerInsights.ENABLED)

        self.task_execution_role = iam.Role(
            self, "TaskExecutionRole",
            role_name="devopshero-ecs-task-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")],
        )
        # Allow ECS to inject secrets as env vars (e.g., Aurora credentials)
        self.task_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{Aws.REGION}:{Aws.ACCOUNT_ID}:secret:devopshero/*"],
        ))

        self.log_group = logs.LogGroup(
            self, "EcsLogGroup", log_group_name="/devopshero/ecs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.DESTROY,
        )

        CfnOutput(self, "ClusterArn", value=self.cluster.cluster_arn, export_name="devopshero-cluster-arn")
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name, export_name="devopshero-cluster-name")
        CfnOutput(self, "TaskExecutionRoleArn", value=self.task_execution_role.role_arn, export_name="devopshero-task-execution-role-arn")
        CfnOutput(self, "LogGroupName", value=self.log_group.log_group_name, export_name="devopshero-ecs-log-group")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_or_create_vpc_cidr(session: boto3.Session) -> str:
    """Get existing VPC CIDR or find an available one."""
    cf_client = session.client("cloudformation")
        
    vpc_stack_name = "devopshero-vpc"
    if cloudformation_utils.stack_exists(cf_client, vpc_stack_name):
        print(f"\n📦 VPC stack '{vpc_stack_name}' already exists, getting existing CIDR...")
        vpc_cidr = cloudformation_utils.get_stack_output(cf_client, vpc_stack_name, "VpcCidr")
        if not vpc_cidr:
            raise RuntimeError("Could not get VPC CIDR from existing stack")
        print(f"   ✅ Using existing VPC CIDR: {vpc_cidr}")
        return vpc_cidr
    else:
        print(f"\n📦 VPC stack '{vpc_stack_name}' does not exist, finding available CIDR...")
        ec2_client = session.client("ec2")
        vpc_cidr = vpc_utils.find_available_vpc_cidr(ec2_client)["VpcCidr"]
        print(f"   ✅ Found available VPC CIDR: {vpc_cidr}")
        return vpc_cidr


# =============================================================================
# DEPLOYMENT FUNCTIONS
# =============================================================================


def deploy(
    session: boto3.Session,
    synth_only: bool,
) -> bool:
    """
    Deploy shared infrastructure: VPC and ECS cluster.
    
    Args:
        session: Boto3 session with assumed role credentials
        synth_only: If True, only synthesize templates, don't deploy
    
    Returns:
        True on success, False on failure
    """
    print(f"\n{'='*60}")
    print(f"🚀 Deploying shared infrastructure")
    print(f"{'='*60}")
    
    vpc_cidr = get_or_create_vpc_cidr(session)
    
    cdk_app = App(outdir=str(cdk_utils.CDK_OUT_DIR))
    
    vpc_stack = VpcStack(cdk_app, "devopshero-vpc", vpc_cidr=vpc_cidr)
    ecs_cluster_stack = EcsClusterStack(cdk_app, "devopshero-ecs-cluster", vpc=vpc_stack.vpc)
    ecs_cluster_stack.add_dependency(vpc_stack)
    
    if synth_only:
        cloud_assembly = cdk_app.synth()
        print(f"\n✅ CDK templates synthesized to: {cloud_assembly.directory}")
        return True
    
    success = cdk_utils.deploy_cdk_stacks(cdk_app, session)
    
    if success:
        print(f"\n{'='*60}")
        print("✅ Infrastructure deployment complete!")
        print(f"{'='*60}")
    
    return success


def teardown(session: boto3.Session) -> bool:
    """
    Delete shared infrastructure stacks (ECS cluster, VPC).
    WARNING: This will fail if any app stacks still depend on them.
    """
    cf_client = session.client("cloudformation")
    
    stacks_to_delete = [
        "devopshero-ecs-cluster",
        "devopshero-vpc",
    ]
    
    print(f"\n{'='*60}")
    print(f"🗑️  Tearing down shared infrastructure")
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
        print("✅ Infrastructure stacks deleted successfully")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print("⚠️  Some stacks failed to delete (apps may still depend on them)")
        print(f"{'='*60}")
    
    return all_success
