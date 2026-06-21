"""Storage Stack for DevOps Hero - S3 buckets, ECR repository, and EFS."""

import os

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_efs as efs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3_deployment
from constructs import Construct


class StorageStack(Stack):
    """S3 buckets, ECR repository, and EFS for DevOps Hero."""

    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Public bucket for CloudFormation templates and Lambda code that customers download
        self.public_bucket = s3.Bucket(
            self,
            "PublicBucket",
            bucket_name="humr-public",
            versioned=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False,
            ),
            # CORS required for CloudFormation Quick Create Stack (browser fetches template)
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )
        # Allow public read access
        self.public_bucket.grant_public_access()

        # Deploy CloudFormation templates to public bucket
        # These templates are used by customers to connect their AWS accounts
        infra_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        s3_deployment.BucketDeployment(
            self,
            "PublicBucketTemplates",
            sources=[s3_deployment.Source.asset(infra_dir, exclude=["*", "!cf_install_template.json"])],
            destination_bucket=self.public_bucket,
        )

        # Private bucket for internal assets (encrypted)
        self.private_bucket = s3.Bucket(
            self,
            "PrivateBucket",
            bucket_name="humr-private",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ECR Repository for Docker images
        self.ecr_repository = ecr.Repository(
            self,
            "EcrRepository",
            repository_name="humr",
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            lifecycle_rules=[ecr.LifecycleRule(description="Keep last 10 images", max_image_count=10, rule_priority=1)],
        )

        # EFS for Claude session persistence across deployments
        # Claude SDK stores conversation sessions locally; EFS makes them survive container replacements
        self.efs_security_group = ec2.SecurityGroup(
            self,
            "EfsSecurityGroup",
            vpc=vpc,
            security_group_name="humr-prod-efs-sg",
            description="Security group for EFS mount targets",
            allow_all_outbound=False,
        )
        # Allow NFS access from anywhere in the VPC (ECS tasks run in private subnets)
        self.efs_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(2049),
            description="Allow NFS from VPC",
        )
        self.claude_efs = efs.FileSystem(
            self,
            "ClaudeEfs",
            file_system_name="humr-prod-claude-sessions",
            vpc=vpc,
            security_group=self.efs_security_group,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.BURSTING,
            removal_policy=RemovalPolicy.RETAIN,
            encrypted=True,
        )
        # EFS Access Point — a custom entry door with pre-configured user identity.
        # Without this, EFS mounts are root-owned and non-root containers can't write.
        # The Access Point enforces:
        #   - posix_user: All file operations are performed as UID 1000, regardless of caller
        #   - path: The container sees /appuser-claude as its root (chroot-like)
        #   - create_acl: Auto-creates the directory with correct ownership if missing
        self.claude_efs_access_point = self.claude_efs.add_access_point(
            "AppUserAccessPoint",
            path="/appuser-claude",
            create_acl=efs.Acl(owner_uid="1000", owner_gid="1000", permissions="755"),
            posix_user=efs.PosixUser(uid="1000", gid="1000"),
        )

        CfnOutput(self, "EfsFileSystemId", value=self.claude_efs.file_system_id, export_name="humr-prod-efs-id")
        CfnOutput(self, "PublicBucketName", value=self.public_bucket.bucket_name, export_name="humr-prod-public-bucket")
        CfnOutput(self, "PublicBucketUrl", value=f"https://{self.public_bucket.bucket_name}.s3.amazonaws.com", export_name="humr-prod-public-bucket-url")
        CfnOutput(self, "PrivateBucketName", value=self.private_bucket.bucket_name, export_name="humr-prod-private-bucket")
        CfnOutput(self, "PrivateBucketArn", value=self.private_bucket.bucket_arn, export_name="humr-prod-private-bucket-arn")
        CfnOutput(self, "EcrRepositoryUri", value=self.ecr_repository.repository_uri, export_name="humr-prod-ecr-uri")
        CfnOutput(self, "EcrRepositoryArn", value=self.ecr_repository.repository_arn, export_name="humr-prod-ecr-arn")
