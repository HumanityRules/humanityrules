"""
Deploy DevOpsHero apps (ECR, ALB, ECS service) using AWS CDK.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import boto3
from aws_cdk import App, Aws, CfnOutput, Duration, Fn, RemovalPolicy, SecretValue, Stack, Tags
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_efs as efs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from . import appconfig
from . import auth_lambda
from . import cdk_utils
from . import cloudformation_utils
from . import deploy_base
from . import ecr_utils
from . import route53_utils
from . import secrets_utils


# Sidecar image published once per env into doh/{env_slug}/sidecar:{tag}. Keep
# this pinned here rather than on AppConfig: the sidecar is DOH-owned, not
# AppTemplate-driven, and a version bump is a platform operation.
SIDECAR_IMAGE_VERSION = "0.1.0"
SIDECAR_SOURCE_DIR = Path(__file__).resolve().parents[3] / "sidecar"


def sidecar_ecr_repo_name(env_slug: str) -> str:
    """Per-env ECR repo for the sidecar image: doh/{env_slug}/sidecar."""
    return f"doh/{env_slug}/sidecar"


def _resolve_pdp_url() -> str:
    """Resolve the PDP URL the sidecar should call. DOH_PDP_URL wins if set."""
    import os
    from django.conf import settings
    explicit = os.environ.get("DOH_PDP_URL")
    if explicit:
        return explicit
    # In prod the sidecar calls devopshero.ai directly. In local dev the
    # sidecar lives in a customer VPC and can't reach the laptop, so we
    # point it at a reserved ngrok tunnel that forwards to localhost:8000.
    # If someone else ever needs to deploy a sidecar'd app from their
    # laptop, switch to a per-developer DOH_PDP_PUBLIC_URL setting.
    base = "https://devopshero.ai" if not settings.DEBUG else "https://devopshero.ngrok.io"
    return f"{base}/api/pdp/evaluate"

logger = logging.getLogger(__name__)


@dataclass
class DeployResult:
    success: bool
    error: str        # failure reason (empty on success)
    service_url: str  # best URL (HTTPS if available, else HTTP ALB)
    alb_dns: str      # raw ALB DNS hostname


# =============================================================================
# CDK STACKS
# =============================================================================

AURORA_MYSQL_DEFAULT_VERSION = "3.08.0"
AURORA_POSTGRES_DEFAULT_VERSION = "16.4"


def get_engine_port(engine_family: str) -> int:
    if engine_family == "aurora-postgresql":
        return 5432
    if engine_family == "aurora-mysql":
        return 3306
    raise ValueError(f"Unsupported engine family: {engine_family}")


def get_engine_scheme(engine_family: str) -> str:
    if engine_family == "aurora-postgresql":
        return "postgresql"
    if engine_family == "aurora-mysql":
        return "mysql"
    raise ValueError(f"Unsupported engine family: {engine_family}")


def get_engine_version(engine_config: appconfig.EngineConfig) -> "rds.IClusterEngine":
    """Get CDK engine version from config. Uses sensible defaults if version not specified."""
    if engine_config.family == "aurora-mysql":
        version_str = engine_config.version or AURORA_MYSQL_DEFAULT_VERSION
        major = version_str.split(".")[0]
        version = rds.AuroraMysqlEngineVersion.of(
            aurora_mysql_full_version=version_str,
            aurora_mysql_major_version=major,
        )
        return rds.DatabaseClusterEngine.aurora_mysql(version=version)
    if engine_config.family == "aurora-postgresql":
        version_str = engine_config.version or AURORA_POSTGRES_DEFAULT_VERSION
        major = version_str.split(".")[0]
        version = rds.AuroraPostgresEngineVersion.of(
            aurora_postgres_full_version=version_str,
            aurora_postgres_major_version=major,
        )
        return rds.DatabaseClusterEngine.aurora_postgres(version=version)
    raise ValueError(f"Unsupported engine family: {engine_config.family}")


def get_connection_env_var_name(connection_config: appconfig.ConnectionConfig) -> str:
    return connection_config.env_var_name or "DATABASE_URL"


def dockerfile_containers(app_config: appconfig.AppConfig) -> list[appconfig.ContainerConfig]:
    """Return the subset of containers that DOH builds from source at deploy time."""
    return [c for c in app_config.containers if c.image_source == "dockerfile"]


def prebuilt_containers(app_config: appconfig.AppConfig) -> list[appconfig.ContainerConfig]:
    """Return the subset of containers that reference a pre-pushed ECR image."""
    return [c for c in app_config.containers if c.image_source == "prebuilt"]


def _missing_prebuilt_images(
    session: boto3.Session,
    app_config: appconfig.AppConfig,
    env_slug: str,
) -> list[str]:
    """Return '{repo}:{tag}' identifiers for prebuilt containers whose image is absent from ECR.

    Hard-fails before CDK runs — a deploy with a dangling prebuilt reference
    would only surface as an ECS pull error hours later.
    """
    from botocore.exceptions import ClientError

    ecr_client = session.client("ecr")
    missing: list[str] = []
    for c in prebuilt_containers(app_config):
        repo_name = f"doh/{env_slug}/{c.prebuilt_ecr_repo}"
        try:
            ecr_client.describe_images(
                repositoryName=repo_name,
                imageIds=[{"imageTag": c.prebuilt_version}],
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("RepositoryNotFoundException", "ImageNotFoundException"):
                missing.append(f"{repo_name}:{c.prebuilt_version}")
                continue
            raise
    return missing


def _container_image_uri(
    container: appconfig.ContainerConfig,
    account: str,
    region: str,
    env_slug: str,
    app_image_tag: str,
) -> str:
    """Resolve the ECR image URI for a container.

    - `dockerfile`: {account}.dkr.ecr.{region}.amazonaws.com/{c.ecr_repo_name}:{app_image_tag}
    - `prebuilt`:   {account}.dkr.ecr.{region}.amazonaws.com/doh/{env_slug}/{c.prebuilt_ecr_repo}:{c.prebuilt_version}
    """
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com"
    if container.image_source == "dockerfile":
        assert container.ecr_repo_name, "dockerfile container must have ecr_repo_name"
        return f"{registry}/{container.ecr_repo_name}:{app_image_tag}"
    if container.image_source == "prebuilt":
        assert container.prebuilt_ecr_repo and container.prebuilt_version, (
            "prebuilt container must have prebuilt_ecr_repo + prebuilt_version"
        )
        return f"{registry}/doh/{env_slug}/{container.prebuilt_ecr_repo}:{container.prebuilt_version}"
    raise ValueError(f"Unknown image_source='{container.image_source}' on container '{container.name}'")


class EcrStack(Stack):
    """
    DevOpsHero ECR Stack — one ECR repo per dockerfile-built container in the app.

    Containers with image_source="prebuilt" are expected to already exist in a
    separate per-env ECR repo (doh/{env_slug}/<repo>:<version>) and are not
    created here.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        app_config: appconfig.AppConfig,
        resource_prefix: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.repositories: dict[str, ecr.Repository] = {}
        for idx, c in enumerate(dockerfile_containers(app_config)):
            assert c.ecr_repo_name is not None, "dockerfile container must have ecr_repo_name"
            repo = ecr.Repository(
                self, f"EcrRepository{idx}",
                repository_name=c.ecr_repo_name,
                image_scan_on_push=True,
                removal_policy=RemovalPolicy.DESTROY,
                empty_on_delete=True,
                lifecycle_rules=[ecr.LifecycleRule(description="Keep last 10 images", max_image_count=10, rule_priority=1)],
            )
            Tags.of(repo).add("App", app_config.app_name)
            Tags.of(repo).add("Container", c.name)
            self.repositories[c.name] = repo

            # Export the URI/ARN for the primary (first) container under the
            # legacy output names so consumers of the CFN exports keep working.
            if idx == 0:
                CfnOutput(self, "EcrRepositoryUri", value=repo.repository_uri, export_name=f"{resource_prefix}-ecr-uri")
                CfnOutput(self, "EcrRepositoryArn", value=repo.repository_arn, export_name=f"{resource_prefix}-ecr-arn")


class SidecarEcrStack(Stack):
    """Per-env ECR repo for the DOH sidecar image.

    One repo per environment: doh/{env_slug}/sidecar. Shared by every
    sidecar-enabled app in the env. Created once per env on the first
    sidecar-enabled deploy and then imported from subsequent deploys.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_slug: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        repo_name = sidecar_ecr_repo_name(env_slug)
        self.repository = ecr.Repository(
            self, "SidecarEcrRepository",
            repository_name=repo_name,
            image_scan_on_push=True,
            # Keep older sidecar images around: a sidecar version bump that
            # needs to be rolled back mustn't be blocked by the lifecycle policy.
            lifecycle_rules=[ecr.LifecycleRule(
                description="Keep last 20 sidecar images", max_image_count=20, rule_priority=1,
            )],
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )
        Tags.of(self.repository).add("Env", env_slug)
        Tags.of(self.repository).add("Component", "sidecar")

        CfnOutput(
            self, "SidecarEcrRepositoryUri",
            value=self.repository.repository_uri,
            export_name=f"devopshero-{env_slug}-sidecar-ecr-uri",
        )


class AuroraClusterStack(Stack):
    """
    Aurora cluster for apps that need a database.
    Creates a connection secret derived from the Aurora-managed secret.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        app_config: appconfig.AppConfig,
        env_slug: str,
        resource_prefix: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        database_config = app_config.database_config
        if not database_config:
            raise ValueError("DatabaseConfig is required for Aurora cluster creation")

        # Import environment infrastructure (Aurora doesn't need shared ALB info, pass None)
        self.environment_infra = deploy_base.import_environment_infrastructure(
            scope=self,
            env_slug=env_slug,
            shared_alb_hosted_zone=None,
        )

        # Validate database name: alphanumeric and underscores, 1-64 chars, must start with letter
        db_name = database_config.name
        if not db_name or len(db_name) > 64:
            raise ValueError("Database name must be 1-64 characters")
        if not db_name[0].isalpha():
            raise ValueError("Database name must start with a letter")
        if not all(c.isalnum() or c == "_" for c in db_name):
            raise ValueError("Database name must contain only alphanumeric characters and underscores")

        # Validate backup retention: Aurora limits are 1-35 days
        retention_days = database_config.backups.retention_days
        if retention_days < 1 or retention_days > 35:
            raise ValueError("Backup retention days must be between 1 and 35")

        engine = get_engine_version(database_config.engine)
        engine_port = get_engine_port(database_config.engine.family)

        # Security group for Aurora - allows MySQL access from VPC
        self.security_group = ec2.SecurityGroup(
            self, "AuroraSecurityGroup",
            vpc=self.environment_infra.vpc,
            description="Security group for Aurora - allows database access from VPC",
            allow_all_outbound=True,
        )
        # Allow database access from the default security group (used by ECS tasks)
        self.security_group.add_ingress_rule(
            peer=self.environment_infra.default_security_group, connection=ec2.Port.tcp(engine_port), description="Allow database access from ECS tasks",
        )

        # Subnet group for Aurora (private subnets)
        subnet_group = rds.SubnetGroup(
            self, "AuroraSubnetGroup",
            description="Subnet group for Aurora",
            vpc=self.environment_infra.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            removal_policy=RemovalPolicy.DESTROY,
        )

        deployment = database_config.deployment
        if deployment.mode == "aurora_serverless_v2":
            if not deployment.serverless_v2:
                raise ValueError("Serverless v2 config is required for aurora_serverless_v2")
            if deployment.provisioned:
                raise ValueError("Provisioned config must be null for aurora_serverless_v2")
            writer = rds.ClusterInstance.serverless_v2("writer")
            serverless_min_capacity = deployment.serverless_v2.min_acu
            serverless_max_capacity = deployment.serverless_v2.max_acu
            if serverless_min_capacity > serverless_max_capacity:
                raise ValueError("Serverless v2 min_acu must be <= max_acu")
            if round(serverless_min_capacity * 2) != serverless_min_capacity * 2:
                raise ValueError("Serverless v2 min_acu must be in 0.5 increments")
            if round(serverless_max_capacity * 2) != serverless_max_capacity * 2:
                raise ValueError("Serverless v2 max_acu must be in 0.5 increments")
            if serverless_min_capacity < 0.5 or serverless_min_capacity > 128:
                raise ValueError("Serverless v2 min_acu must be between 0.5 and 128")
            if serverless_max_capacity < 0.5 or serverless_max_capacity > 128:
                raise ValueError("Serverless v2 max_acu must be between 0.5 and 128")
        elif deployment.mode == "aurora_provisioned":
            if not deployment.provisioned:
                raise ValueError("Provisioned config is required for aurora_provisioned")
            if deployment.serverless_v2:
                raise ValueError("Serverless v2 config must be null for aurora_provisioned")
            instance_class = deployment.provisioned.instance_class
            instance_type = ec2.InstanceType(instance_class.removeprefix("db."))
            writer = rds.ClusterInstance.provisioned(
                "writer",
                instance_type=instance_type,
                auto_minor_version_upgrade=database_config.engine.auto_minor_version_upgrade,
            )
            serverless_min_capacity = None
            serverless_max_capacity = None
        else:
            raise ValueError(f"Unsupported deployment mode: {deployment.mode}")

        cluster_identifier = f"{resource_prefix}-aurora"[:63]
        secret_name = f"devopshero/{env_slug}/{app_config.app_name}/aurora/credentials"

        self.cluster = rds.DatabaseCluster(
            self, "AuroraCluster",
            engine=engine,
            cluster_identifier=cluster_identifier,
            default_database_name=database_config.name,
            credentials=rds.Credentials.from_generated_secret("dbadmin", secret_name=secret_name),
            vpc=self.environment_infra.vpc,
            subnet_group=subnet_group,
            security_groups=[self.security_group],
            serverless_v2_min_capacity=serverless_min_capacity,
            serverless_v2_max_capacity=serverless_max_capacity,
            writer=writer,
            readers=[],
            storage_encrypted=database_config.security.storage_encrypted,
            backup=rds.BackupProps(retention=Duration.days(database_config.backups.retention_days)),
            copy_tags_to_snapshot=database_config.backups.copy_tags_to_snapshot,
            deletion_protection=database_config.security.deletion_protection,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Connection data is provided via a derived Secrets Manager secret
        self.endpoint = self.cluster.cluster_endpoint.hostname
        self.port = str(self.cluster.cluster_endpoint.port)
        self.secret_arn = self.cluster.secret.secret_arn

        connection_secret_name = f"devopshero/{env_slug}/{app_config.app_name}/aurora/connection"
        self.connection_secret = self._create_connection_secret(
            connection_secret_name=connection_secret_name,
            engine_family=database_config.engine.family,
        )
        self.connection_secret.node.add_dependency(self.cluster)

        Tags.of(self.cluster).add("App", app_config.app_name)
        Tags.of(self.connection_secret).add("App", app_config.app_name)

        CfnOutput(self, "ClusterEndpoint", value=self.endpoint, export_name=f"{resource_prefix}-aurora-endpoint")
        CfnOutput(self, "ClusterPort", value=self.port, export_name=f"{resource_prefix}-aurora-port")
        CfnOutput(self, "DatabaseName", value=database_config.name, export_name=f"{resource_prefix}-aurora-database")
        CfnOutput(self, "SecretArn", value=self.secret_arn, export_name=f"{resource_prefix}-aurora-secret-arn")
        CfnOutput(self, "ConnectionSecretArn", value=self.connection_secret.secret_arn, export_name=f"{resource_prefix}-aurora-connection-secret-arn")

    def _create_connection_secret(
        self,
        connection_secret_name: str,
        engine_family: str,
    ) -> secretsmanager.Secret:
        aurora_secret = self.cluster.secret
        if not aurora_secret:
            raise ValueError("Aurora secret is required for connection secret creation")

        secret_id = aurora_secret.secret_arn
        username_ref = SecretValue.secrets_manager(secret_id=secret_id, json_field="username").to_string()
        password_ref = SecretValue.secrets_manager(secret_id=secret_id, json_field="password").to_string()
        host_ref = SecretValue.secrets_manager(secret_id=secret_id, json_field="host").to_string()
        port_ref = SecretValue.secrets_manager(secret_id=secret_id, json_field="port").to_string()
        dbname_ref = SecretValue.secrets_manager(secret_id=secret_id, json_field="dbname").to_string()

        scheme = get_engine_scheme(engine_family)
        database_url = f"{scheme}://{username_ref}:{password_ref}@{host_ref}:{port_ref}/{dbname_ref}"

        return secretsmanager.Secret(
            self,
            "ConnectionSecret",
            secret_name=connection_secret_name,
            secret_object_value={
                "url": SecretValue.unsafe_plain_text(database_url),
                "host": SecretValue.secrets_manager(secret_id=secret_id, json_field="host"),
                "port": SecretValue.secrets_manager(secret_id=secret_id, json_field="port"),
                "dbname": SecretValue.secrets_manager(secret_id=secret_id, json_field="dbname"),
                "username": SecretValue.secrets_manager(secret_id=secret_id, json_field="username"),
                "password": SecretValue.secrets_manager(secret_id=secret_id, json_field="password"),
            },
        )


def _compute_listener_rule_priority(app_name: str) -> int:
    """Compute a deterministic listener rule priority from app name."""
    # Use hash to get a deterministic priority. Range: 1000-41000 (leaving room for manual overrides)
    return (hash(app_name) % 40000) + 1000


class AppStack(Stack):
    """DevOpsHero App Stack - ECS Service with shared ALB routing."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        app_config: appconfig.AppConfig,
        image_tag: str,
        env_slug: str,
        resource_prefix: str,
        subdomain: str,
        database_connection_secret: secretsmanager.ISecret | None,
        shared_alb_hosted_zone: str | None,
        shared_hosted_zone_id: str | None,
        sidecar_shared_secrets_arn: str | None,
        sidecar_image_version: str | None,
        auth_base_url: str | None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        alb_target = app_config.alb_target()
        if alb_target is None:
            raise RuntimeError(
                f"App '{app_config.app_name}' has no alb_target_container; "
                f"ALB attachment requires one container in the list to be marked as the target."
            )

        # Auth sidecar is opt-in per AppTemplate. When enabled the task def adds a
        # third container in front of the ALB-target container, ALB routes to
        # that sidecar, and the sidecar proxies to the target over localhost.
        sidecar_enabled = bool(app_config.sidecar_enabled)
        if sidecar_enabled and not (
            sidecar_shared_secrets_arn and sidecar_image_version and auth_base_url
        ):
            raise RuntimeError(
                "Sidecar-enabled deploy requires sidecar_shared_secrets_arn, "
                "sidecar_image_version, and auth_base_url. One or more were missing — "
                "did the orchestration skip ensure_env_sidecar_secrets_exist?",
            )
        # Auth sidecar listens on alb_target.container_port + 1; the app keeps its original port.
        sidecar_listen_port = alb_target.container_port + 1 if sidecar_enabled else None
        # The target group port (= ALB forwarding port) points at whichever
        # container should receive inbound traffic.
        target_port = sidecar_listen_port if sidecar_enabled else alb_target.container_port

        # Import environment infrastructure
        self.environment_infra = deploy_base.import_environment_infrastructure(
            scope=self,
            env_slug=env_slug,
            shared_alb_hosted_zone=shared_alb_hosted_zone,
        )

        # Per-app task role for secret isolation - each app can only read its own secrets
        task_role = iam.Role(
            self, "TaskRole",
            role_name=f"{resource_prefix}-task-role"[:64],
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # Grant access to this app's secrets (created outside CDK via ensure_app_secrets_exist)
        if app_config.app_secrets:
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[f"arn:aws:secretsmanager:{Aws.REGION}:{Aws.ACCOUNT_ID}:secret:devopshero/{env_slug}/{app_config.app_name}/*"],
            ))
        if database_connection_secret:
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[database_connection_secret.secret_arn],
            ))
        if sidecar_enabled:
            # The sidecar container reads DOH_SIDECAR_TOKEN from the env's
            # shared-secrets entry via ECS secret injection.
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[sidecar_shared_secrets_arn],
            ))

        # EFS: create per-app access point and grant mount permissions. The
        # volume is declared once at the task level; containers that opt in
        # (c.efs_mount) each add their own MountPoint below.
        efs_access_point = None
        if app_config.efs_config:
            efs_file_system = efs.FileSystem.from_file_system_attributes(
                self, "ImportedEfs",
                file_system_id=self.environment_infra.efs_file_system_id,
                security_group=self.environment_infra.efs_security_group,
            )
            uid = str(app_config.efs_config.posix_uid)
            gid = str(app_config.efs_config.posix_gid)
            efs_access_point = efs.AccessPoint(
                self, "AppAccessPoint",
                file_system=efs_file_system,
                path=f"/deployments/{app_config.app_name}",
                create_acl=efs.Acl(owner_uid=uid, owner_gid=gid, permissions="755"),
                posix_user=efs.PosixUser(uid=uid, gid=gid),
            )
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"],
                resources=[efs_file_system.file_system_arn],
                conditions={
                    "StringEquals": {
                        "elasticfilesystem:AccessPointArn": efs_access_point.access_point_arn,
                    },
                },
            ))

        # Database env vars + secrets are projected into the ALB-target container only —
        # sibling containers (sidecars, MCP servers, etc.) have no database contract.
        alb_target_database_secrets: dict[str, ecs.Secret] = {}
        if database_connection_secret and app_config.database_config:
            env_var_name = get_connection_env_var_name(app_config.database_config.connection)
            alb_target_database_secrets[env_var_name] = ecs.Secret.from_secrets_manager(database_connection_secret, field="url")
            alb_target_database_secrets["DATABASE_HOST"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="host")
            alb_target_database_secrets["DATABASE_PORT"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="port")
            alb_target_database_secrets["DATABASE_NAME"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="dbname")
            alb_target_database_secrets["DATABASE_USERNAME"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="username")
            alb_target_database_secrets["DATABASE_PASSWORD"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="password")

        # Shared task-level Secrets Manager bag. Each container only sees the
        # fields it declared in its own ContainerConfig.app_secrets.
        app_secret_resource: secretsmanager.ISecret | None = None
        if app_config.app_secrets:
            app_secret_resource = secretsmanager.Secret.from_secret_name_v2(
                self, "AppSecret", f"devopshero/{env_slug}/{app_config.app_name}/secrets",
            )

        task_definition = ecs.FargateTaskDefinition(
            self, "TaskDefinition",
            family=resource_prefix[:255],
            cpu=app_config.cpu,
            memory_limit_mib=app_config.memory,
            execution_role=self.environment_infra.task_execution_role,
            task_role=task_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        if efs_access_point:
            task_definition.add_volume(
                name="app-workspace",
                efs_volume_configuration=ecs.EfsVolumeConfiguration(
                    file_system_id=self.environment_infra.efs_file_system_id,
                    transit_encryption="ENABLED",
                    authorization_config=ecs.AuthorizationConfig(
                        access_point_id=efs_access_point.access_point_id,
                        iam="ENABLED",
                    ),
                ),
            )

        # Add each configured container to the task definition.
        containers_by_name: dict[str, ecs.ContainerDefinition] = {}
        for idx, c in enumerate(app_config.containers):
            image_uri = _container_image_uri(
                container=c,
                account=self.account,
                region=self.region,
                env_slug=env_slug,
                app_image_tag=image_tag,
            )

            # Environment: per-container list of {name, value}.
            environment = {e["name"]: e["value"] for e in c.environment_variables}

            # Secrets: the container's declared fields from the shared app_secrets bag,
            # plus database_connection_secret pieces on the ALB-target container only.
            secrets: dict[str, ecs.Secret] = {}
            if app_secret_resource is not None and c.app_secrets:
                for field_name in c.app_secrets:
                    secrets[field_name] = ecs.Secret.from_secrets_manager(app_secret_resource, field=field_name)
            if c.name == alb_target.name:
                secrets.update(alb_target_database_secrets)

            health_check = None
            if c.health_check_command:
                health_check = ecs.HealthCheck(
                    command=["CMD-SHELL", c.health_check_command],
                    interval=Duration.seconds(30),
                    timeout=Duration.seconds(10),
                    retries=3,
                    start_period=Duration.seconds(c.health_check_grace_period or 60),
                )

            container = task_definition.add_container(
                f"Container{idx}",
                container_name=f"{app_config.app_name}-{c.name}",
                image=ecs.ContainerImage.from_registry(image_uri),
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix=f"{app_config.app_name}-{c.name}",
                    log_group=self.environment_infra.log_group,
                ),
                environment=environment or None,
                secrets=secrets if secrets else None,
                health_check=health_check,
            )
            # Only the ALB-target container needs a port mapping visible to ECS
            # task-networking — sibling containers communicate over the task's
            # shared loopback where no mapping is required.
            if c.name == alb_target.name:
                container.add_port_mappings(
                    ecs.PortMapping(container_port=c.container_port, protocol=ecs.Protocol.TCP),
                )

            if efs_access_point and c.efs_mount:
                assert app_config.efs_config is not None  # guaranteed by the `if efs_access_point` branch
                container.add_mount_points(
                    ecs.MountPoint(
                        container_path=app_config.efs_config.mount_path,
                        source_volume="app-workspace",
                        read_only=False,
                    ),
                )

            containers_by_name[c.name] = container

        # Auth sidecar: adds a third container in front of the ALB-target
        # container, redirects the ALB to it, and proxies to the target over
        # localhost. Orthogonal to the `containers` list — auth sidecar stays
        # on its own `sidecar_enabled` flag.
        if sidecar_enabled:
            sidecar_image_uri = (
                f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/"
                f"{sidecar_ecr_repo_name(env_slug)}:{sidecar_image_version}"
            )
            sidecar_shared_secret = secretsmanager.Secret.from_secret_complete_arn(
                self, "SidecarSharedSecret", sidecar_shared_secrets_arn,
            )
            pdp_url = _resolve_pdp_url()
            sidecar_environment = {
                "DOH_APP_ID": app_config.app_name,
                "DOH_ENV_SLUG": env_slug,
                "DOH_ENV_DOMAIN": shared_alb_hosted_zone or "",
                "DOH_AUTH_BASE_URL": auth_base_url,
                "DOH_JWKS_URL": f"{auth_base_url.rstrip('/')}/.well-known/jwks.json",
                "DOH_PDP_URL": pdp_url,
                "DOH_UPSTREAM_HOST": "127.0.0.1",
                "DOH_UPSTREAM_PORT": str(alb_target.container_port),
                "DOH_LISTEN_PORT": str(sidecar_listen_port),
            }
            sidecar_container = task_definition.add_container(
                "SidecarContainer",
                container_name=f"{app_config.app_name}-sidecar",
                image=ecs.ContainerImage.from_registry(sidecar_image_uri),
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix=f"{app_config.app_name}-sidecar",
                    log_group=self.environment_infra.log_group,
                ),
                environment=sidecar_environment,
                secrets={
                    "DOH_SIDECAR_TOKEN": ecs.Secret.from_secrets_manager(
                        sidecar_shared_secret, field="DOH_SIDECAR_TOKEN",
                    ),
                },
            )
            sidecar_container.add_port_mappings(
                ecs.PortMapping(container_port=sidecar_listen_port, protocol=ecs.Protocol.TCP),
            )
            # ALB-target container must be listening before the sidecar accepts traffic.
            sidecar_container.add_container_dependencies(
                ecs.ContainerDependency(
                    container=containers_by_name[alb_target.name],
                    condition=ecs.ContainerDependencyCondition.START,
                ),
            )

        # When DOH runs in production (DEBUG=False), use stable settings
        # When developing locally (DEBUG=True), use aggressive settings for fast deploys
        from django.conf import settings
        if settings.DEBUG:
            deregistration_delay = 0
            health_check_interval = 5
            healthy_threshold = 2   # ALB minimum is 2
            min_healthy = 0
            health_check_grace = 0
        else:
            deregistration_delay = 30
            health_check_interval = 10
            healthy_threshold = 2
            min_healthy = 0       # Should it be 100 for "always on, zero downtime"? Make it an option for the user?
            health_check_grace = 60

        if alb_target.health_check_grace_period is not None:
            health_check_grace = alb_target.health_check_grace_period

        # When the sidecar is the ALB target, the ALB's health check must hit
        # a route the sidecar handles locally (bypassing the PDP), otherwise
        # ALB probes would all 302 to auth and never go healthy. The sidecar
        # exposes /__sidecar/healthz for exactly this.
        if sidecar_enabled:
            target_health_check_path = "/__sidecar/healthz"
            target_health_check_codes = "200"
        else:
            target_health_check_path = alb_target.health_check_path or "/"
            # 301 accepted: the ALB terminates SSL and forwards to the container over HTTP, adding
            # X-Forwarded-Proto: https so the app knows the original request was secure. Frameworks like
            # Phoenix (force_ssl) and Rails (force_ssl) check this header and pass traffic through.
            # But ALB health checks are synthetic HTTP requests without X-Forwarded-Proto, so apps
            # with force_ssl redirect them to HTTPS (301). Accepting 301 as healthy handles this.
            target_health_check_codes = "200,301"

        target_group = elbv2.ApplicationTargetGroup(
            self, "TargetGroup",
            target_group_name=f"doh-{env_slug}-{app_config.app_name}"[:32].rstrip("-"),
            vpc=self.environment_infra.vpc,
            port=target_port,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            deregistration_delay=Duration.seconds(deregistration_delay),
            health_check=elbv2.HealthCheck(
                enabled=True, path=target_health_check_path, protocol=elbv2.Protocol.HTTP,
                interval=Duration.seconds(health_check_interval), timeout=Duration.seconds(2),
                healthy_threshold_count=healthy_threshold, unhealthy_threshold_count=3,
                healthy_http_codes=target_health_check_codes,
            ),
        )

        # Configure routing rules on the shared ALB and create per-app DNS record
        self._setup_shared_alb_routing(
            app_config=app_config,
            subdomain=subdomain,
            env_slug=env_slug,
            resource_prefix=resource_prefix,
            target_group=target_group,
            shared_alb_hosted_zone=shared_alb_hosted_zone,
            shared_hosted_zone_id=shared_hosted_zone_id,
        )

        # Circuit breaker: for desired_count=1, ECS trips after 3 consecutive
        # failed task starts (~3-6 min) instead of CFN's 3h stabilization wait.
        # rollback=True auto-reverts to the prior COMPLETED deployment on trip;
        # on first deploy there is none, so the stack simply rolls back via CFN.
        service = ecs.FargateService(
            self, "EcsService",
            service_name=resource_prefix[:255],
            cluster=self.environment_infra.cluster,
            task_definition=task_definition,
            desired_count=1,
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[self.environment_infra.default_security_group],
            enable_execute_command=True,
            min_healthy_percent=min_healthy,
            max_healthy_percent=200,
            health_check_grace_period=Duration.seconds(health_check_grace),
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
        )
        # Multiple containers in the task — be explicit about which one the
        # ALB targets. The sidecar overrides this when enabled.
        if sidecar_enabled:
            target_group.add_target(service.load_balancer_target(
                container_name=f"{app_config.app_name}-sidecar",
                container_port=sidecar_listen_port,
            ))
        else:
            target_group.add_target(service.load_balancer_target(
                container_name=f"{app_config.app_name}-{alb_target.name}",
                container_port=alb_target.container_port,
            ))

        Tags.of(service).add("App", app_config.app_name)
        Tags.of(task_definition).add("App", app_config.app_name)

        CfnOutput(self, "TaskDefinitionArn", value=task_definition.task_definition_arn, export_name=f"{resource_prefix}-task-def-arn")
        CfnOutput(self, "ServiceArn", value=service.service_arn, export_name=f"{resource_prefix}-service-arn")
        CfnOutput(self, "ServiceName", value=service.service_name, export_name=f"{resource_prefix}-service-name")

    def _setup_shared_alb_routing(
        self,
        app_config: appconfig.AppConfig,
        subdomain: str,
        env_slug: str,
        resource_prefix: str,
        target_group: elbv2.ApplicationTargetGroup,
        shared_alb_hosted_zone: str | None,
        shared_hosted_zone_id: str | None,
    ) -> None:
        """Configure routing rules on the shared ALB and create per-app DNS record."""
        # Use subdomain for listener priority to ensure uniqueness per Route53 record
        priority = _compute_listener_rule_priority(subdomain)
        prefix = f"devopshero-{env_slug}"

        # Import shared ALB DNS for output
        shared_alb_dns = Fn.import_value(f"{prefix}-shared-alb-dns")

        # Add HTTP listener rule (always)
        http_listener = elbv2.ApplicationListener.from_application_listener_attributes(
            self, "ImportedHttpListener",
            listener_arn=self.environment_infra.shared_alb_http_listener_arn,
            security_group=self.environment_infra.shared_alb_security_group,
        )

        # Determine the host header for routing
        if shared_alb_hosted_zone:
            # Use subdomain + hosted zone for the hostname (subdomain may differ from app_name)
            app_hostname = f"{subdomain}.{shared_alb_hosted_zone}"
            host_condition = elbv2.ListenerCondition.host_headers([app_hostname])
        else:
            # HTTP-only mode: route by path prefix since no domain
            app_hostname = None
            host_condition = elbv2.ListenerCondition.path_patterns([f"/{subdomain}/*"])

        elbv2.ApplicationListenerRule(
            self, "HttpListenerRule",
            listener=http_listener,
            priority=priority,
            conditions=[host_condition],
            target_groups=[target_group],
        )

        # Add HTTPS listener rule if hosted zone is configured
        if shared_alb_hosted_zone and self.environment_infra.shared_alb_https_listener_arn:
            https_listener = elbv2.ApplicationListener.from_application_listener_attributes(
                self, "ImportedHttpsListener",
                listener_arn=self.environment_infra.shared_alb_https_listener_arn,
                security_group=self.environment_infra.shared_alb_security_group,
            )

            elbv2.ApplicationListenerRule(
                self, "HttpsListenerRule",
                listener=https_listener,
                priority=priority,
                conditions=[elbv2.ListenerCondition.host_headers([app_hostname])],
                target_groups=[target_group],
            )

            CfnOutput(self, "HttpsUrl", value=f"https://{app_hostname}", export_name=f"{resource_prefix}-https-url")

        # Create per-app DNS record pointing to this environment's ALB
        # This allows multiple environments to share the same hosted zone without conflicts
        if shared_alb_hosted_zone and shared_hosted_zone_id and app_hostname:
            hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
                self, "HostedZone",
                hosted_zone_id=shared_hosted_zone_id,
                zone_name=shared_alb_hosted_zone,
            )

            # Import the shared ALB for the Route53 alias target
            # DNS name and canonical hosted zone ID are required for Route53 alias records
            shared_alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
                self, "ImportedSharedAlb",
                load_balancer_arn=Fn.import_value(f"{prefix}-shared-alb-arn"),
                security_group_id=self.environment_infra.shared_alb_security_group.security_group_id,
                load_balancer_dns_name=shared_alb_dns,
                load_balancer_canonical_hosted_zone_id=Fn.import_value(f"{prefix}-shared-alb-canonical-hz-id"),
            )

            route53.ARecord(
                self, "AppDnsRecord",
                zone=hosted_zone,
                record_name=app_hostname,
                target=route53.RecordTarget.from_alias(targets.LoadBalancerTarget(shared_alb)),
            )

        CfnOutput(self, "SharedAlbDns", value=shared_alb_dns, export_name=f"{resource_prefix}-shared-alb-dns")


# =============================================================================
# DEPLOYMENT FUNCTIONS
# =============================================================================


def deploy(
    session: boto3.Session,
    account_id: str,
    region: str,
    app_config: appconfig.AppConfig,
    image_tag: str,
    env_slug: str,
    subdomain: str,
    synth_only: bool,
    shared_alb_hosted_zone: str | None,
) -> DeployResult:
    """
    Deploy an app to existing infrastructure.

    Assumes VPC and ECS cluster are already deployed (run deploy_base first).

    Args:
        session: Boto3 session with assumed role credentials.
        account_id: Target AWS account ID.
        region: Target AWS region.
        app_config: Application configuration.
        image_tag: Docker image tag to deploy.
        env_slug: Environment slug (e.g., "default", "prod").
        subdomain: Route53 subdomain for this deployment (may differ from app name).
        synth_only: If True, only synthesize templates, don't deploy.
        shared_alb_hosted_zone: Hosted zone for shared ALB (e.g., "dev.example.com"). None = HTTP only.
    Returns:
        DeployResult with success flag and extracted URLs.
    """
    logger.info("Deploying app '%(app_name)s' to environment '%(env_slug)s'", {"app_name": app_config.app_name, "env_slug": env_slug})

    # Resource prefix for consistent naming: doh-{env}-{app} (app slugs are per organization unique)
    resource_prefix = f"doh-{env_slug}-{app_config.app_name}"

    cf_client = session.client("cloudformation")

    app_stack_names = [f"{resource_prefix}-ecr", f"{resource_prefix}-app"]
    if app_config.database_config:
        app_stack_names.append(f"{resource_prefix}-aurora")
    cloudformation_utils.cleanup_rollback_complete_stacks(cf_client, app_stack_names)

    # Verify infrastructure exists
    vpc_stack_name = f"devopshero-{env_slug}-vpc"
    cluster_stack_name = f"devopshero-{env_slug}-cluster"

    if not cloudformation_utils.stack_exists(cf_client, vpc_stack_name):
        msg = f"Base layer not deployed. VPC stack '{vpc_stack_name}' not found"
        logger.error(msg)
        return DeployResult(success=False, error=msg, service_url="", alb_dns="")
    if not cloudformation_utils.stack_exists(cf_client, cluster_stack_name):
        msg = f"ECS cluster not deployed. Cluster stack '{cluster_stack_name}' not found"
        logger.error(msg)
        return DeployResult(success=False, error=msg, service_url="", alb_dns="")

    # Resolve shared + app-level secrets in Secrets Manager (created outside CDK for security)
    if app_config.app_secrets:
        logger.info("Ensuring app secrets exist")
        shared_secrets = secrets_utils.get_shared_secrets(session=session, env_slug=env_slug)
        secrets_utils.ensure_app_secrets_exist(session=session, env_slug=env_slug, app_config=app_config, shared_secrets=shared_secrets)

    # Look up hosted zone ID for per-app DNS record creation
    shared_hosted_zone_id = None
    if shared_alb_hosted_zone:
        logger.info("Looking up hosted zone ID for '%(hosted_zone)s'", {"hosted_zone": shared_alb_hosted_zone})
        shared_hosted_zone_id = route53_utils.get_hosted_zone_id(session=session, hosted_zone_name=shared_alb_hosted_zone)
        if shared_hosted_zone_id:
            logger.info("Found hosted zone ID: %(hosted_zone_id)s", {"hosted_zone_id": shared_hosted_zone_id})
        else:
            logger.error("Could not find hosted zone ID for '%(hosted_zone)s', DNS record will not be created", {"hosted_zone": shared_alb_hosted_zone})

    # Sidecar prerequisites: per-env secrets + ECR repo + image push + auth Lambda.
    # All idempotent, safe to run on every sidecar-enabled deploy. The first
    # sidecar-enabled deploy in an env does the heavy lift; subsequent deploys
    # are fast because the secrets, stacks, and image already exist.
    sidecar_secret_arns: dict[str, str] = {}
    sidecar_auth_base_url: str | None = None
    if app_config.sidecar_enabled:
        if not shared_alb_hosted_zone or not shared_hosted_zone_id:
            msg = "Sidecar-enabled apps require a hosted zone (HTTPS)"
            logger.error(msg)
            return DeployResult(success=False, error=msg, service_url="", alb_dns="")

        logger.info("Ensuring per-env sidecar infrastructure exists")
        from devopshero_app.models import Environment
        env_obj = Environment.objects.get(slug=env_slug)
        sidecar_secret_arns = secrets_utils.ensure_env_sidecar_secrets_exist(
            session=session, env=env_obj,
        )
        sidecar_auth_base_url = f"https://auth.{shared_alb_hosted_zone}"

    cdk_app = App(outdir=str(cdk_utils.CDK_OUT_DIR))

    ecr_stack = EcrStack(
        cdk_app,
        f"{resource_prefix}-ecr",
        app_config=app_config,
        resource_prefix=resource_prefix,
    )

    sidecar_ecr_stack = None
    auth_lambda_stack = None
    if app_config.sidecar_enabled:
        sidecar_ecr_stack = SidecarEcrStack(
            cdk_app,
            f"devopshero-{env_slug}-sidecar-ecr",
            env_slug=env_slug,
        )
        auth_lambda_stack = auth_lambda.AuthLambdaStack(
            cdk_app,
            f"devopshero-{env_slug}-auth-lambda",
            inputs=auth_lambda.AuthLambdaInputs(
                env_slug=env_slug,
                env_domain=shared_alb_hosted_zone,
                shared_alb_https_listener_arn=Fn.import_value(
                    f"devopshero-{env_slug}-shared-alb-https-listener-arn",
                ),
                shared_alb_security_group_id=Fn.import_value(
                    f"devopshero-{env_slug}-shared-alb-sg-id",
                ),
                shared_hosted_zone_id=shared_hosted_zone_id,
                shared_hosted_zone_name=shared_alb_hosted_zone,
                sidecar_auth_config_secret_arn=sidecar_secret_arns["sidecar_auth_config_arn"],
            ),
        )

    # Optionally create Aurora cluster (imports VPC from environment's VPC stack exports)
    aurora_stack = None
    aurora_connection_secret = None
    if app_config.database_config:
        aurora_stack = AuroraClusterStack(
            scope=cdk_app,
            construct_id=f"{resource_prefix}-aurora",
            app_config=app_config,
            env_slug=env_slug,
            resource_prefix=resource_prefix,
        )
        aurora_connection_secret = aurora_stack.connection_secret

    app_stack = AppStack(
        scope=cdk_app,
        construct_id=f"{resource_prefix}-app",
        app_config=app_config,
        image_tag=image_tag,
        env_slug=env_slug,
        resource_prefix=resource_prefix,
        subdomain=subdomain,
        database_connection_secret=aurora_connection_secret,
        shared_alb_hosted_zone=shared_alb_hosted_zone,
        shared_hosted_zone_id=shared_hosted_zone_id,
        sidecar_shared_secrets_arn=sidecar_secret_arns.get("shared_secrets_arn") if app_config.sidecar_enabled else None,
        sidecar_image_version=SIDECAR_IMAGE_VERSION if app_config.sidecar_enabled else None,
        auth_base_url=sidecar_auth_base_url,
    )
    app_stack.add_dependency(ecr_stack)
    if aurora_stack:
        app_stack.add_dependency(aurora_stack)

    assembly_dir = cdk_utils.synth_cdk_app(cdk_app)

    if synth_only:
        return DeployResult(success=True, error="", service_url="", alb_dns="")

    # Phase 1a: Sidecar ECR + auth Lambda stacks (sidecar-enabled apps only).
    if app_config.sidecar_enabled:
        sidecar_pre_stacks = [
            f"devopshero-{env_slug}-sidecar-ecr",
            f"devopshero-{env_slug}-auth-lambda",
        ]
        if not cdk_utils.deploy_from_assembly(assembly_dir=assembly_dir, session=session, stack_names=sidecar_pre_stacks):
            logger.error("CDK deployment failed (sidecar-ecr/auth-lambda)")
            return DeployResult(success=False, error="CDK deployment failed (sidecar infra)", service_url="", alb_dns="")

        # Push the DOH-owned sidecar image into the per-env repo. This is
        # idempotent: if the tag already exists in ECR the push is a no-op.
        logger.info("Building and pushing sidecar image (%s)", SIDECAR_IMAGE_VERSION)
        sidecar_image_uri = ecr_utils.build_and_push_docker_image(
            session=session,
            account_id=account_id,
            region=region,
            env_slug=env_slug,
            app_name="sidecar",
            ecr_repo_name=sidecar_ecr_repo_name(env_slug),
            app_source_path=SIDECAR_SOURCE_DIR,
            image_tag=SIDECAR_IMAGE_VERSION,
        )
        if not sidecar_image_uri:
            logger.error("Sidecar image build/push failed")
            return DeployResult(success=False, error="Sidecar image build/push failed", service_url="", alb_dns="")

    # Phase 1b: Deploy ECR repo (and Aurora if needed) so the registry exists before the app image push
    pre_app_stacks = [f"{resource_prefix}-ecr"]
    if aurora_stack:
        pre_app_stacks.append(f"{resource_prefix}-aurora")

    if not cdk_utils.deploy_from_assembly(assembly_dir=assembly_dir, session=session, stack_names=pre_app_stacks):
        logger.error("CDK deployment failed (ECR/Aurora)")
        return DeployResult(success=False, error="CDK deployment failed (ECR/Aurora)", service_url="", alb_dns="")

    # Phase 2a: Verify all prebuilt containers exist in ECR before we start
    # building the dockerfile ones. Hard-fail here with a clear operator
    # message — a missing prebuilt image would only surface as an ECS pull
    # error hours later, long after the CDK deploy succeeded.
    missing = _missing_prebuilt_images(
        session=session,
        app_config=app_config,
        env_slug=env_slug,
    )
    if missing:
        msg = (
            "Missing prebuilt images in ECR:\n  - "
            + "\n  - ".join(missing)
            + "\nBuild each with:\n  uv run manage.py doh_build_prebuilt_image "
            "--account <acct> [--env <env>] --source-dir <path> --ecr-repo <repo> --tag <tag>"
        )
        logger.error(msg)
        return DeployResult(success=False, error=msg, service_url="", alb_dns="")

    # Phase 2b: Build and push every dockerfile container's image from the
    # cloned source tree at app_source_path. Today all dockerfile containers
    # in a template share the same source tree (the template's clone root) —
    # their container.dockerfile_path distinguishes them if needed. A future
    # monorepo multi-image use case will need per-container subpaths; that
    # requirement doesn't exist yet.
    for c in dockerfile_containers(app_config):
        logger.info("Building and pushing image for container '%s'", c.name)
        assert c.ecr_repo_name is not None
        image_uri = ecr_utils.build_and_push_docker_image(
            session=session,
            account_id=account_id,
            region=region,
            env_slug=env_slug,
            app_name=f"{app_config.app_name}-{c.name}",
            ecr_repo_name=c.ecr_repo_name,
            app_source_path=app_config.app_source_path,
            image_tag=image_tag,
        )
        if not image_uri:
            logger.error("Docker build/push failed for container '%s'", c.name)
            return DeployResult(
                success=False,
                error=f"Docker build/push failed for container '{c.name}'",
                service_url="", alb_dns="",
            )

    # Phase 3: Deploy the App stack (image exists, so ECS can start tasks immediately)
    if not cdk_utils.deploy_from_assembly(assembly_dir=assembly_dir, session=session, stack_names=[f"{resource_prefix}-app"]):
        logger.error("CDK deployment failed (App)")
        return DeployResult(success=False, error="CDK deployment failed (App)", service_url="", alb_dns="")

    logger.info("Deployment of %(app_name)s completed successfully", {"app_name": app_config.app_name})

    # Extract URLs from CloudFormation outputs
    urls = cloudformation_utils.get_app_urls(cf_client, app_name=app_config.app_name, env_slug=env_slug, has_domain=bool(shared_alb_hosted_zone))
    service_url = urls.get("https_url") or urls.get("alb_url") or ""
    alb_url = urls.get("alb_url") or ""
    alb_dns = alb_url.removeprefix("http://")

    cloudformation_utils.print_deployment_summary(
        account_id=account_id,
        region=region,
        app_name=app_config.app_name,
        image_tag=image_tag,
        service_url=service_url,
        alb_dns=alb_dns,
    )

    return DeployResult(success=True, error="", service_url=service_url, alb_dns=alb_dns)


def teardown(
    session: boto3.Session,
    app_config: appconfig.AppConfig,
    env_slug: str,
) -> bool:
    """
    Delete app-specific CDK stacks (ECR, ALB, ECS service, Aurora if applicable).
    """
    cf_client = session.client("cloudformation")

    resource_prefix = f"doh-{env_slug}-{app_config.app_name}"

    # App-specific stacks in reverse dependency order
    stacks_to_delete = [
        f"{resource_prefix}-app",
    ]

    # Add Aurora stack if the app uses a database
    if app_config.database_config:
        stacks_to_delete.append(f"{resource_prefix}-aurora")

    stacks_to_delete.append(f"{resource_prefix}-ecr")

    logger.info("Tearing down app: %(app_name)s", {"app_name": app_config.app_name})
    logger.info("Stacks to delete (in order):")
    for stack in stacks_to_delete:
        logger.info("   - %(stack_name)s", {"stack_name": stack})

    # Empty every per-app ECR repository first — CloudFormation can't delete
    # non-empty repos. Prebuilt-container repos are per-env shared resources
    # and are not torn down here.
    for c in dockerfile_containers(app_config):
        assert c.ecr_repo_name is not None
        ecr_utils.delete_all_ecr_images(session=session, ecr_repo_name=c.ecr_repo_name)

    all_success = True
    for stack_name in stacks_to_delete:
        success = cloudformation_utils.delete_stack_and_wait(cf_client, stack_name=stack_name)
        if not success:
            all_success = False

    if all_success:
        logger.info("App '%(app_name)s' stacks deleted successfully", {"app_name": app_config.app_name})
        return all_success

    logger.error("Some stacks failed to delete")

    return all_success
