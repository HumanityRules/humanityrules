"""
Deploy shared DevOpsHero infrastructure (VPC, ECS cluster, shared ALB, EFS) using AWS CDK.
"""

from dataclasses import dataclass
import logging

import boto3
from aws_cdk import App, Aws, CfnOutput, Fn, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_efs as efs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from constructs import Construct

from . import acm_utils
from . import cdk_utils
from . import cloudformation_utils
from . import route53_utils
from . import vpc_utils


logger = logging.getLogger(__name__)


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
    # Shared ALB resources
    shared_alb_http_listener_arn: str
    shared_alb_https_listener_arn: str | None
    shared_alb_security_group: ec2.ISecurityGroup
    shared_alb_hosted_zone: str | None
    # Builder resources (builder_instance_id is looked up at runtime via ec2_builder_utils)
    builder_security_group: ec2.ISecurityGroup | None
    # Shared EFS for persistent app workspaces (e.g., AI agent memory)
    efs_file_system_id: str
    efs_security_group: ec2.ISecurityGroup


def import_environment_infrastructure(scope: Construct, env_slug: str, shared_alb_hosted_zone: str | None) -> EnvironmentInfrastructure:
    """
    Import VPC, security group, cluster, shared ALB, and other base infrastructure from an environment.

    Must be called from within a Stack context since it creates CDK constructs.

    Args:
        scope: The CDK construct scope (typically 'self' from within a Stack).
        env_slug: Environment slug (e.g., "default", "prod").
        shared_alb_hosted_zone: The hosted zone for the shared ALB (used to determine if HTTPS is available).

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

    # Import shared ALB resources (always present)
    shared_alb_http_listener_arn = Fn.import_value(f"{prefix}-shared-alb-http-listener-arn")
    shared_alb_security_group = ec2.SecurityGroup.from_security_group_id(
        scope, "ImportedSharedAlbSg",
        Fn.import_value(f"{prefix}-shared-alb-sg-id"),
    )

    # HTTPS listener is only present if hosted zone was configured
    shared_alb_https_listener_arn = None
    if shared_alb_hosted_zone:
        shared_alb_https_listener_arn = Fn.import_value(f"{prefix}-shared-alb-https-listener-arn")

    # Import builder security group
    builder_security_group = ec2.SecurityGroup.from_security_group_id(
        scope, "ImportedBuilderSg",
        Fn.import_value(f"{prefix}-builder-sg-id"),
    )

    # Import shared EFS resources
    efs_file_system_id = Fn.import_value(f"{prefix}-efs-id")
    efs_security_group = ec2.SecurityGroup.from_security_group_id(
        scope, "ImportedEfsSg",
        Fn.import_value(f"{prefix}-efs-sg-id"),
    )

    return EnvironmentInfrastructure(
        vpc=vpc,
        default_security_group=default_security_group,
        cluster=cluster,
        task_execution_role=task_execution_role,
        log_group=log_group,
        shared_alb_http_listener_arn=shared_alb_http_listener_arn,
        shared_alb_https_listener_arn=shared_alb_https_listener_arn,
        shared_alb_security_group=shared_alb_security_group,
        shared_alb_hosted_zone=shared_alb_hosted_zone,
        builder_security_group=builder_security_group,
        efs_file_system_id=efs_file_system_id,
        efs_security_group=efs_security_group,
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
    DevOpsHero ECS Cluster Stack - Fargate cluster, IAM roles, and shared ALB for app deployments.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_slug: str,
        vpc: ec2.IVpc,
        shared_hosted_zone_name: str | None,
        shared_hosted_zone_id: str | None,
        existing_certificate_arn: str | None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"devopshero-{env_slug}"

        # ECS Cluster
        self.cluster = ecs.Cluster(
            self, "EcsCluster",
            cluster_name=f"{prefix}-cluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENHANCED,
        )

        # Task Execution Role
        self.task_execution_role = iam.Role(
            self, "TaskExecutionRole",
            role_name=f"{prefix}-task-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")],
        )
        # Allow ECS to inject secrets as env vars (e.g., Aurora credentials)
        self.task_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{Aws.REGION}:{Aws.ACCOUNT_ID}:secret:devopshero/*"],
        ))

        # Log Group
        self.log_group = logs.LogGroup(
            self, "EcsLogGroup",
            log_group_name=f"/devopshero/{env_slug}/ecs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Shared ALB Security Group - always created
        self.alb_security_group = ec2.SecurityGroup(
            self, "SharedAlbSecurityGroup",
            vpc=vpc,
            security_group_name=f"{prefix}-shared-alb-sg",
            description="Security group for shared ALB - allows HTTP (and HTTPS if hosted zone configured)",
            allow_all_outbound=True,
        )
        self.alb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
            description="Allow HTTP from anywhere",
        )

        # Shared ALB - always created
        self.shared_alb = elbv2.ApplicationLoadBalancer(
            self, "SharedAlb",
            load_balancer_name=f"doh-{env_slug}-shared"[:32],
            vpc=vpc,
            internet_facing=True,
            security_group=self.alb_security_group,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        # HTTP Listener - always created with default 404 action
        self.http_listener = self.shared_alb.add_listener(
            "HttpListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_action=elbv2.ListenerAction.fixed_response(
                status_code=404,
                content_type="text/plain",
                message_body="No app configured for this host",
            ),
        )

        # HTTPS setup - only if hosted zone is provided
        self.https_listener = None
        self.wildcard_certificate = None
        if shared_hosted_zone_name and shared_hosted_zone_id:
            # Allow HTTPS traffic
            self.alb_security_group.add_ingress_rule(
                peer=ec2.Peer.any_ipv4(),
                connection=ec2.Port.tcp(443),
                description="Allow HTTPS from anywhere",
            )

            # Import the hosted zone
            hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
                self, "HostedZone",
                hosted_zone_id=shared_hosted_zone_id,
                zone_name=shared_hosted_zone_name,
            )

            # Use existing certificate if available, otherwise create new
            if existing_certificate_arn:
                self.wildcard_certificate = acm.Certificate.from_certificate_arn(
                    self, "WildcardCertificate",
                    certificate_arn=existing_certificate_arn,
                )
            else:
                self.wildcard_certificate = acm.Certificate(
                    self, "WildcardCertificate",
                    domain_name=f"*.{shared_hosted_zone_name}",
                    validation=acm.CertificateValidation.from_dns(hosted_zone),
                )

            # HTTPS Listener with default 404 action
            self.https_listener = self.shared_alb.add_listener(
                "HttpsListener",
                port=443,
                protocol=elbv2.ApplicationProtocol.HTTPS,
                certificates=[self.wildcard_certificate],
                ssl_policy=elbv2.SslPolicy.TLS13_RES,
                default_action=elbv2.ListenerAction.fixed_response(
                    status_code=404,
                    content_type="text/plain",
                    message_body="No app configured for this host",
                ),
            )

            # NOTE: No wildcard DNS record here. Each app creates its own DNS record
            # pointing to this environment's ALB. This allows multiple environments
            # to share the same hosted zone without DNS conflicts.

            # Export HTTPS-specific values
            CfnOutput(self, "SharedAlbHttpsListenerArn", value=self.https_listener.listener_arn, export_name=f"{prefix}-shared-alb-https-listener-arn")
            CfnOutput(self, "SharedAlbHostedZone", value=shared_hosted_zone_name, export_name=f"{prefix}-shared-alb-hosted-zone")
            CfnOutput(self, "WildcardCertificateArn", value=self.wildcard_certificate.certificate_arn, export_name=f"{prefix}-wildcard-cert-arn")

        # Export cluster and role values
        CfnOutput(self, "ClusterArn", value=self.cluster.cluster_arn, export_name=f"{prefix}-cluster-arn")
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name, export_name=f"{prefix}-cluster-name")
        CfnOutput(self, "TaskExecutionRoleArn", value=self.task_execution_role.role_arn, export_name=f"{prefix}-task-execution-role-arn")
        CfnOutput(self, "LogGroupName", value=self.log_group.log_group_name, export_name=f"{prefix}-ecs-log-group")

        # Export shared ALB values (always present)
        CfnOutput(self, "SharedAlbArn", value=self.shared_alb.load_balancer_arn, export_name=f"{prefix}-shared-alb-arn")
        CfnOutput(self, "SharedAlbDns", value=self.shared_alb.load_balancer_dns_name, export_name=f"{prefix}-shared-alb-dns")
        CfnOutput(self, "SharedAlbSecurityGroupId", value=self.alb_security_group.security_group_id, export_name=f"{prefix}-shared-alb-sg-id")
        CfnOutput(self, "SharedAlbHttpListenerArn", value=self.http_listener.listener_arn, export_name=f"{prefix}-shared-alb-http-listener-arn")
        CfnOutput(self, "SharedAlbCanonicalHostedZoneId", value=self.shared_alb.load_balancer_canonical_hosted_zone_id, export_name=f"{prefix}-shared-alb-canonical-hz-id")


class BuilderStack(Stack):
    """
    DevOpsHero Builder Stack - EC2 instance for Docker builds with auto-stop watchdog.

    The instance is placed in a private subnet (uses NAT for outbound) with no inbound rules.
    SSM is used for remote access (no SSH keys needed). Docker cache persists on the root EBS
    volume when the instance is stopped. A watchdog service auto-stops the instance after
    15 minutes of inactivity.
    """

    # User data script: install Docker, create build directory, set up watchdog
    USER_DATA_SCRIPT = """#!/bin/bash
set -e

# Install Docker
dnf install -y docker
systemctl enable docker
systemctl start docker
usermod -aG docker ec2-user

# Create build directory
mkdir -p /build
chown ec2-user:ec2-user /build

# Watchdog script: stop instance after 15min idle
# Uses ec2-user home directory for activity file (avoids permission issues)
cat > /usr/local/bin/watchdog.sh << 'WATCHDOG'
#!/bin/bash
IDLE_TIMEOUT=900
ACTIVITY_FILE=/home/ec2-user/last_build_activity
touch $ACTIVITY_FILE
chown ec2-user:ec2-user $ACTIVITY_FILE
while true; do
  sleep 60
  idle=$(($(date +%s) - $(stat -c %Y $ACTIVITY_FILE)))
  if [ $idle -gt $IDLE_TIMEOUT ]; then
    shutdown -h now
  fi
done
WATCHDOG
chmod +x /usr/local/bin/watchdog.sh

# Watchdog systemd service
cat > /etc/systemd/system/watchdog.service << 'SERVICE'
[Unit]
Description=Builder idle watchdog
After=network.target

[Service]
ExecStart=/usr/local/bin/watchdog.sh
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE
systemctl enable watchdog
systemctl start watchdog

# Signal successful initialization
touch /tmp/builder_ready
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

        prefix = f"devopshero-{env_slug}"

        # IAM Role for EC2 instance
        self.instance_role = iam.Role(
            self, "BuilderRole",
            role_name=f"{prefix}-builder-role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                # SSM agent permissions
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )

        # ECR push permissions (for all repositories in the account)
        self.instance_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ecr:GetAuthorizationToken",
            ],
            resources=["*"],
        ))
        self.instance_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
            ],
            resources=[f"arn:aws:ecr:{Aws.REGION}:{Aws.ACCOUNT_ID}:repository/doh/*"],
        ))

        # Security Group - no inbound rules (SSM works outbound-only)
        self.security_group = ec2.SecurityGroup(
            self, "BuilderSecurityGroup",
            vpc=vpc,
            security_group_name=f"{prefix}-builder-sg",
            description="Security group for Docker builder - no inbound (SSM only)",
            allow_all_outbound=True,
        )

        # EC2 Instance - Amazon Linux 2023, t4g.medium (Graviton ARM64), 50GB root volume
        # ARM64 so Docker builds natively match ECS Fargate ARM64 target
        self.instance = ec2.Instance(
            self, "BuilderInstance",
            instance_name=f"{prefix}-builder",
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.M8G, ec2.InstanceSize.LARGE),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(cpu_type=ec2.AmazonLinuxCpuType.ARM_64),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_group=self.security_group,
            role=self.instance_role,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        volume_size=50,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        delete_on_termination=False,  # Preserve Docker cache
                    ),
                ),
            ],
            user_data=ec2.UserData.custom(self.USER_DATA_SCRIPT),
        )

        # Tag for environment identification (used by ec2_builder_utils to find the instance)
        from aws_cdk import Tags
        Tags.of(self.instance).add("devopshero:environment", env_slug)

        # Exports
        CfnOutput(self, "BuilderInstanceId", value=self.instance.instance_id, export_name=f"{prefix}-builder-instance-id")
        CfnOutput(self, "BuilderSecurityGroupId", value=self.security_group.security_group_id, export_name=f"{prefix}-builder-sg-id")


class EfsStack(Stack):
    """
    Shared EFS filesystem for persistent app workspaces (e.g., AI agent memory).

    One filesystem per environment, shared by all apps that need persistent storage.
    Each app creates its own EFS access point for path and UID isolation.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_slug: str,
        vpc: ec2.IVpc,
        vpc_cidr: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"devopshero-{env_slug}"

        self.security_group = ec2.SecurityGroup(
            self, "EfsSecurityGroup",
            vpc=vpc,
            security_group_name=f"{prefix}-efs-sg",
            description="Security group for EFS mount targets - allows NFS from VPC",
            allow_all_outbound=False,
        )
        self.security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc_cidr),
            connection=ec2.Port.tcp(2049),
            description="Allow NFS from VPC",
        )

        self.file_system = efs.FileSystem(
            self, "SharedEfs",
            file_system_name=f"{prefix}-shared",
            vpc=vpc,
            security_group=self.security_group,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.BURSTING,
            removal_policy=RemovalPolicy.RETAIN,
            encrypted=True,
        )

        CfnOutput(self, "EfsFileSystemId", value=self.file_system.file_system_id, export_name=f"{prefix}-efs-id")
        CfnOutput(self, "EfsSecurityGroupId", value=self.security_group.security_group_id, export_name=f"{prefix}-efs-sg-id")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_or_create_vpc_cidr(session: boto3.Session, env_slug: str) -> str:
    """Get existing VPC CIDR or find an available one."""
    cf_client = session.client("cloudformation")

    vpc_stack_name = f"devopshero-{env_slug}-vpc"
    if cloudformation_utils.stack_exists(cf_client, vpc_stack_name):
        logger.info("VPC stack '%(stack_name)s' already exists, getting existing CIDR", {"stack_name": vpc_stack_name})
        vpc_cidr = cloudformation_utils.get_stack_output(cf_client, vpc_stack_name, "VpcCidr")
        logger.info("Using existing VPC CIDR: %(vpc_cidr)s", {"vpc_cidr": vpc_cidr})
    else:
        logger.info("VPC stack '%(stack_name)s' does not exist, finding available CIDR", {"stack_name": vpc_stack_name})
        ec2_client = session.client("ec2")
        vpc_cidr = vpc_utils.find_available_vpc_cidr(ec2_client)["VpcCidr"]
        logger.info("Found available VPC CIDR: %(vpc_cidr)s", {"vpc_cidr": vpc_cidr})

    if not vpc_cidr:
        raise RuntimeError("Could not get VPC CIDR")

    return vpc_cidr



# =============================================================================
# DEPLOYMENT FUNCTIONS
# =============================================================================


def deploy(
    session: boto3.Session,
    env_slug: str,
    synth_only: bool,
    shared_alb_hosted_zone: str | None,
) -> bool:
    """
    Deploy shared infrastructure: VPC, ECS cluster, and shared ALB.

    Args:
        session: Boto3 session with assumed role credentials.
        env_slug: Environment slug for resource naming (e.g., "default", "prod").
        synth_only: If True, only synthesize templates, don't deploy.
        shared_alb_hosted_zone: Hosted zone for wildcard cert (e.g., "dev.example.com"). None = HTTP only.
    Returns:
        True on success, False on failure.
    """
    logger.info("Deploying shared infrastructure for environment '%(env_slug)s'", {"env_slug": env_slug})

    cf_client = session.client("cloudformation")
    vpc_stack_name = f"devopshero-{env_slug}-vpc"
    cluster_stack_name = f"devopshero-{env_slug}-cluster"
    builder_stack_name = f"devopshero-{env_slug}-builder"
    efs_stack_name = f"devopshero-{env_slug}-efs"

    cloudformation_utils.cleanup_rollback_complete_stacks(cf_client, [vpc_stack_name, cluster_stack_name, builder_stack_name, efs_stack_name])

    vpc_cidr = get_or_create_vpc_cidr(session=session, env_slug=env_slug)

    # Look up hosted zone ID if hosted zone name is provided
    shared_hosted_zone_id = None
    existing_certificate_arn = None
    if shared_alb_hosted_zone:
        logger.info("Looking up hosted zone for '%(hosted_zone)s'", {"hosted_zone": shared_alb_hosted_zone})
        shared_hosted_zone_id = route53_utils.get_hosted_zone_id(session=session, hosted_zone_name=shared_alb_hosted_zone)
        if shared_hosted_zone_id:
            logger.info("Found hosted zone: %(hosted_zone_id)s", {"hosted_zone_id": shared_hosted_zone_id})
            # Check if wildcard certificate already exists
            existing_certificate_arn = acm_utils.find_wildcard_certificate(session=session, domain_name=shared_alb_hosted_zone)
            if existing_certificate_arn:
                logger.info("Will use existing wildcard certificate")
            else:
                logger.info("No existing wildcard certificate found, will create new one")
        else:
            logger.error("Could not find hosted zone '%(hosted_zone)s', HTTPS will not be configured", {"hosted_zone": shared_alb_hosted_zone})
            shared_alb_hosted_zone = None  # Fall back to HTTP-only

    cdk_app = App(outdir=str(cdk_utils.CDK_OUT_DIR))

    vpc_stack = VpcStack(cdk_app, vpc_stack_name, env_slug=env_slug, vpc_cidr=vpc_cidr)

    builder_stack = BuilderStack(cdk_app, builder_stack_name, env_slug=env_slug, vpc=vpc_stack.vpc)
    builder_stack.add_dependency(vpc_stack)

    ecs_cluster_stack = EcsClusterStack(
        cdk_app,
        cluster_stack_name,
        env_slug=env_slug,
        vpc=vpc_stack.vpc,
        shared_hosted_zone_name=shared_alb_hosted_zone,
        shared_hosted_zone_id=shared_hosted_zone_id,
        existing_certificate_arn=existing_certificate_arn,
    )
    ecs_cluster_stack.add_dependency(vpc_stack)

    efs_stack = EfsStack(cdk_app, efs_stack_name, env_slug=env_slug, vpc=vpc_stack.vpc, vpc_cidr=vpc_cidr)
    efs_stack.add_dependency(vpc_stack)

    if synth_only:
        cloud_assembly = cdk_app.synth()
        logger.info("CDK templates synthesized to: %(directory)s", {"directory": cloud_assembly.directory})
        return True

    success = cdk_utils.deploy_cdk_stacks(cdk_app, session)

    if success:
        logger.info("Infrastructure deployment complete for environment '%(env_slug)s'", {"env_slug": env_slug})

    return success


def teardown(session: boto3.Session, env_slug: str) -> bool:
    """
    Delete every CloudFormation stack owned by the given environment.

    Stacks are discovered dynamically by name prefix (``devopshero-{env_slug}-``)
    rather than from a hardcoded list, so any stack added later (auth-lambda,
    sidecar-ecr, pdp-mock, ...) is torn down as long as it follows the naming
    convention. Deletion runs in rounds because CloudFormation blocks deletion
    of a stack whose exports are still imported: each round deletes whatever
    is currently leaf, which unblocks the next round.
    """
    cf_client = session.client("cloudformation")
    prefix = f"devopshero-{env_slug}-"

    logger.info("Tearing down infrastructure for environment '%(env_slug)s'", {"env_slug": env_slug})

    max_rounds = 5
    for round_num in range(1, max_rounds + 1):
        remaining = cloudformation_utils.list_stacks_by_prefix(cf_client, prefix=prefix)
        if not remaining:
            logger.info("All stacks for '%(env_slug)s' deleted", {"env_slug": env_slug})
            return True

        logger.info(
            "Teardown round %(round)d — %(count)d stack(s) remaining: %(stacks)s",
            {"round": round_num, "count": len(remaining), "stacks": ", ".join(remaining)},
        )

        progress = False
        for stack_name in remaining:
            if cloudformation_utils.delete_stack_and_wait(cf_client, stack_name=stack_name):
                progress = True

        if not progress:
            logger.error(
                "Teardown stalled after round %(round)d — no stacks deleted this pass. Remaining: %(stacks)s",
                {"round": round_num, "stacks": ", ".join(remaining)},
            )
            return False

    still_present = cloudformation_utils.list_stacks_by_prefix(cf_client, prefix=prefix)
    if still_present:
        logger.error(
            "Teardown exceeded %(max)d rounds. Still present: %(stacks)s",
            {"max": max_rounds, "stacks": ", ".join(still_present)},
        )
        return False

    return True
