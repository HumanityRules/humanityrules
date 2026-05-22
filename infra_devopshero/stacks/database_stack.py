"""Database Stack for DevOps Hero - Aurora Serverless v2 PostgreSQL."""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from constructs import Construct


class DatabaseStack(Stack):
    """Aurora Serverless v2 PostgreSQL database for DevOps Hero."""

    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Security group for Aurora - allows PostgreSQL access from VPC
        self.security_group = ec2.SecurityGroup(
            self,
            "AuroraSecurityGroup",
            vpc=vpc,
            security_group_name="doh-prod-aurora-sg",
            description="Security group for Aurora - allows PostgreSQL from VPC",
            allow_all_outbound=True,
        )
        self.security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/16"),
            connection=ec2.Port.tcp(5432),
            description="Allow PostgreSQL from VPC",
        )

        # Subnet group for Aurora (private subnets)
        subnet_group = rds.SubnetGroup(
            self,
            "AuroraSubnetGroup",
            description="Subnet group for Aurora",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Aurora Serverless v2 PostgreSQL cluster
        # The generated secret contains: host, port, dbname, username, password
        self.cluster = rds.DatabaseCluster(
            self,
            "AuroraCluster",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_16_4),
            cluster_identifier="doh-prod-aurora",
            default_database_name="devopshero",
            credentials=rds.Credentials.from_generated_secret("dbadmin", secret_name="devopshero/prod/aurora/credentials"),
            vpc=vpc,
            subnet_group=subnet_group,
            security_groups=[self.security_group],
            # min=0 enables scale-to-zero auto-pause. Cluster pauses after the duration
            # below of no user-initiated connections; first connection after that resumes
            # in up to ~15s. The job worker's keep-awake middleware (devopshero_app/
            # keep_awake_middleware.py) prevents pausing while authenticated users are
            # actively interacting with the system.
            serverless_v2_min_capacity=0,
            serverless_v2_max_capacity=4,
            serverless_v2_auto_pause_duration=Duration.minutes(10),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            readers=[],
            storage_encrypted=True,
            backup=rds.BackupProps(retention=Duration.days(7)),
            deletion_protection=False,  # Set to True for production
            removal_policy=RemovalPolicy.DESTROY,  # Change to RETAIN for production
            enable_data_api=False,  # Enable RDS Data API for HTTP-based queries
        )

        # Expose the Aurora-generated secret directly
        # The app will construct DATABASE_URL from the individual fields
        self.database_secret = self.cluster.secret

        CfnOutput(self, "ClusterEndpoint", value=self.cluster.cluster_endpoint.hostname, export_name="doh-prod-aurora-endpoint")
        CfnOutput(self, "ClusterPort", value=str(self.cluster.cluster_endpoint.port), export_name="doh-prod-aurora-port")
        CfnOutput(self, "DatabaseName", value="devopshero", export_name="doh-prod-aurora-database")
        CfnOutput(self, "SecretArn", value=self.cluster.secret.secret_arn if self.cluster.secret else "", export_name="doh-prod-aurora-secret-arn")
