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
from . import auth_service
from . import cdk_utils
from . import cloudformation_utils
from . import deploy_base
from . import ecr_utils
from . import route53_utils
from . import secrets_utils


# Policy-proxy image published once per env into doh/{env_slug}/policy-proxy:{tag}.
# Pinned here rather than on AppConfig: the policy proxy is DOH-owned, not
# AppTemplate-driven, and a version bump is a platform operation.
POLICY_PROXY_IMAGE_VERSION = "0.2.0"
POLICY_PROXY_SOURCE_DIR = Path(__file__).resolve().parents[3] / "template_repos" / "policy_proxy"

# A single flag signals deployment-time capabilities that we need to grant to, at least, the ECS task. 
# Check the journal "Hermes Bedrock access moved from static keys to ECS task role" for more details.
PLATFORM_CAPABILITY_BEDROCK_RUNTIME = "bedrock-runtime"

BEDROCK_RUNTIME_ACTIONS = [
    "bedrock:ApplyGuardrail",
    "bedrock:CountTokens",
    "bedrock:GetCustomModel",
    "bedrock:GetFoundationModel",
    "bedrock:GetGuardrail",
    "bedrock:GetImportedModel",
    "bedrock:GetInferenceProfile",
    "bedrock:GetProvisionedModelThroughput",
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
    "bedrock:ListCustomModels",
    "bedrock:ListFoundationModels",
    "bedrock:ListGuardrails",
    "bedrock:ListImportedModels",
    "bedrock:ListInferenceProfiles",
    "bedrock:ListProvisionedModelThroughputs",
    "bedrock:ListPromptRouters",
    "bedrock:ListPrompts",
    "bedrock:RenderPrompt",
]


def policy_proxy_ecr_repo_name(env_slug: str) -> str:
    """Per-env ECR repo for the policy-proxy image: doh/{env_slug}/policy-proxy."""
    return f"doh/{env_slug}/policy-proxy"


def _resolve_control_plane_url() -> str:
    """Resolve DOH's control-plane base URL (prod or dev ngrok tunnel).

    In prod env-resident components call devopshero.ai directly. In local dev
    they live in a customer VPC and can't reach the laptop, so we point them
    at a reserved ngrok tunnel that forwards to localhost:8000. If someone
    else ever needs to deploy from their laptop, switch to a per-developer
    setting.
    """
    from django.conf import settings
    return "https://devopshero.ai" if not settings.DEBUG else "https://devopshero.ngrok.io"


def _resolve_pdp_url() -> str:
    """Resolve the PDP URL the policy proxy should call. DOH_PDP_URL wins if set."""
    import os
    explicit = os.environ.get("DOH_PDP_URL")
    if explicit:
        return explicit
    return f"{_resolve_control_plane_url()}/api/pdp/evaluate"

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


def _container_dependency_condition(cond: str) -> ecs.ContainerDependencyCondition:
    mapping = {
        "START": ecs.ContainerDependencyCondition.START,
        "HEALTHY": ecs.ContainerDependencyCondition.HEALTHY,
        "COMPLETE": ecs.ContainerDependencyCondition.COMPLETE,
        "SUCCESS": ecs.ContainerDependencyCondition.SUCCESS,
    }
    if cond not in mapping:
        msg = f"Invalid container depends_on condition: {cond!r}"
        raise ValueError(msg)
    return mapping[cond]


def dockerfile_containers(app_config: appconfig.AppConfig) -> list[appconfig.ContainerConfig]:
    """Return the subset of containers that DOH builds from source at deploy time."""
    return [c for c in app_config.containers if c.image_source == appconfig.ImageSource.DOCKERFILE]


def prebuilt_containers(app_config: appconfig.AppConfig) -> list[appconfig.ContainerConfig]:
    """Return the subset of containers that reference a pre-pushed ECR image."""
    return [c for c in app_config.containers if c.image_source == appconfig.ImageSource.PREBUILT]


def _uses_bedrock_runtime(app_config: appconfig.AppConfig) -> bool:
    """Return True when the template permits Bedrock and effective LLM config selects it."""
    if PLATFORM_CAPABILITY_BEDROCK_RUNTIME not in app_config.platform_capabilities:
        return False

    for c in app_config.containers:
        for env_var in c.environment_variables:
            if env_var.get("name") in {"DOH_LLM_PROVIDER", "DOH_AUX_PROVIDER"}:
                if str(env_var.get("value", "")).lower() == "bedrock":
                    return True
    return False


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


def _environment_has_ec2_capacity_provider(session: boto3.Session, env_slug: str) -> bool:
    """Return True when the environment cluster is associated with the EC2 capacity provider."""
    ecs_client = session.client("ecs")
    cluster_name = f"devopshero-{env_slug}-cluster"
    capacity_provider_name = deploy_base.ec2_capacity_provider_name(env_slug=env_slug)
    response = ecs_client.describe_clusters(clusters=[cluster_name])
    clusters = response.get("clusters", [])
    if not clusters:
        return False
    return capacity_provider_name in clusters[0].get("capacityProviders", [])


def _container_image_uri(
    container: appconfig.ContainerConfig,
    account: str,
    region: str,
    env_slug: str,
    app_image_tag: str,
) -> str:
    """Resolve the ECR image URI for a container, dispatching on ImageSource."""
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com"
    if container.image_source == appconfig.ImageSource.DOCKERFILE:
        assert container.ecr_repo_name, "dockerfile container must have ecr_repo_name"
        return f"{registry}/{container.ecr_repo_name}:{app_image_tag}"
    if container.image_source == appconfig.ImageSource.PREBUILT:
        assert container.prebuilt_ecr_repo and container.prebuilt_version, (
            "prebuilt container must have prebuilt_ecr_repo + prebuilt_version"
        )
        return f"{registry}/doh/{env_slug}/{container.prebuilt_ecr_repo}:{container.prebuilt_version}"
    if container.image_source == appconfig.ImageSource.REGISTRY:
        assert container.registry_image, "registry container must have registry_image"
        return container.registry_image
    if container.image_source == appconfig.ImageSource.POLICY_PROXY:
        return f"{registry}/{policy_proxy_ecr_repo_name(env_slug)}:{POLICY_PROXY_IMAGE_VERSION}"
    raise ValueError(f"Unknown image_source='{container.image_source}' on container '{container.name}'")


class EcrStack(Stack):
    """
    DevOpsHero ECR Stack — one ECR repo per dockerfile-built container in the app.

    Containers with other ImageSource values (prebuilt, registry, policy_proxy)
    are not created here; see the ImageSource enum for where each is sourced.
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


class PolicyProxyEcrStack(Stack):
    """Per-env ECR repo for the DOH policy-proxy image.

    One repo per environment: doh/{env_slug}/policy-proxy. Shared by every app
    in the env that runs behind a policy proxy. Created once per env on the
    first policy-proxy deploy and then imported from subsequent deploys.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_slug: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        repo_name = policy_proxy_ecr_repo_name(env_slug)
        self.repository = ecr.Repository(
            self, "PolicyProxyEcrRepository",
            repository_name=repo_name,
            image_scan_on_push=True,
            # Keep older policy-proxy images around: a version bump that needs
            # to be rolled back mustn't be blocked by the lifecycle policy.
            lifecycle_rules=[ecr.LifecycleRule(
                description="Keep last 20 policy-proxy images", max_image_count=20, rule_priority=1,
            )],
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )
        Tags.of(self.repository).add("Env", env_slug)
        Tags.of(self.repository).add("Component", "policy-proxy")

        CfnOutput(
            self, "PolicyProxyEcrRepositoryUri",
            value=self.repository.repository_uri,
            export_name=f"devopshero-{env_slug}-policy-proxy-ecr-uri",
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
        env_bearer_shared_secrets_arn: str | None,
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

        # Every container with requires_env_bearer needs the env's shared-secrets
        # ARN (to mount DOH_ENV_BEARER via ECS secret injection). The policy
        # proxy needs it too, and additionally requires auth_base_url + upstream
        # wiring. Validate both preconditions before building resources.
        if app_config.needs_env_bearer() and not env_bearer_shared_secrets_arn:
            raise RuntimeError(
                "App declares requires_env_bearer but env_bearer_shared_secrets_arn "
                "is missing — did the orchestration skip ensure_env_bearer_token_exists?",
            )

        # Policy proxy (SSO + ABAC) is opt-in: a template declares a container
        # with image_source=POLICY_PROXY and points alb_target_container at
        # it. No separate flag — presence of the container drives everything.
        policy_proxy = app_config.policy_proxy_container()
        if policy_proxy is not None:
            if not auth_base_url:
                raise RuntimeError(
                    "Policy-proxy deploy requires auth_base_url. Missing — did the "
                    "orchestration skip ensure_env_policy_proxy_secrets_exist?",
                )
            if not policy_proxy.upstream_container:
                raise RuntimeError(
                    f"Policy-proxy container '{policy_proxy.name}' has no upstream_container",
                )
            upstream = next((c for c in app_config.containers if c.name == policy_proxy.upstream_container), None)
            if upstream is None:
                raise RuntimeError(
                    f"Policy-proxy container '{policy_proxy.name}' references upstream "
                    f"'{policy_proxy.upstream_container}' not present in containers "
                    f"{[c.name for c in app_config.containers]}",
                )
            if alb_target.name != policy_proxy.name:
                raise RuntimeError(
                    f"When a policy proxy is declared it must be the alb_target_container "
                    f"(got '{alb_target.name}', expected '{policy_proxy.name}')",
                )
        else:
            upstream = None
        # Target-group port points at whichever container owns the ALB target
        # (which is the policy proxy itself when one is declared).
        target_port = alb_target.container_port

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
        if app_config.needs_env_bearer():
            # Any container with requires_env_bearer reads DOH_ENV_BEARER
            # from the env's shared-secrets entry via ECS secret injection.
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[env_bearer_shared_secrets_arn],
            ))
        if _uses_bedrock_runtime(app_config):
            task_role.add_to_policy(iam.PolicyStatement(
                actions=BEDROCK_RUNTIME_ACTIONS,
                resources=["*"],
            ))

        # EFS: create one AccessPoint per declared mount and grant task-role
        # permission for exactly those ARNs. Per-container mounting happens
        # below via c.efs_mounts (names referring back into this list).
        efs_mount_resources: dict[str, tuple[efs.AccessPoint, str]] = {}  # name -> (access_point, volume_name)
        if app_config.efs_config:
            efs_file_system = efs.FileSystem.from_file_system_attributes(
                self, "ImportedEfs",
                file_system_id=self.environment_infra.efs_file_system_id,
                security_group=self.environment_infra.efs_security_group,
            )
            seen_names: set[str] = set()
            for m in app_config.efs_config.mounts:
                if m.name in seen_names:
                    raise ValueError(f"Duplicate EFS mount name '{m.name}' in efs_config.mounts")
                seen_names.add(m.name)
                subpath = m.subpath
                if subpath.startswith("/") or ".." in subpath.split("/"):
                    raise ValueError(f"EFS mount subpath must be relative without '..': {subpath!r}")
                uid = str(m.posix_uid)
                gid = str(m.posix_gid)
                access_point = efs.AccessPoint(
                    self, f"AppEfs-{m.name}",
                    file_system=efs_file_system,
                    path=f"/deployments/{app_config.app_name}/{subpath}",
                    create_acl=efs.Acl(owner_uid=uid, owner_gid=gid, permissions="755"),
                    posix_user=efs.PosixUser(uid=uid, gid=gid),
                )
                efs_mount_resources[m.name] = (access_point, f"app-efs-{m.name}")
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"],
                resources=[efs_file_system.file_system_arn],
                conditions={
                    "StringEquals": {
                        "elasticfilesystem:AccessPointArn": [ap.access_point_arn for ap, _ in efs_mount_resources.values()],
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

        if app_config.compute_mode == "fargate" and any(c.privileged for c in app_config.containers):
            raise ValueError("Privileged containers are only supported for EC2-backed ECS tasks")
        if app_config.compute_mode == "fargate" and any(c.host_mounts for c in app_config.containers):
            raise ValueError("Host bind mounts are only supported for EC2-backed ECS tasks")
        if app_config.compute_mode == "fargate" and any(c.linux_capabilities for c in app_config.containers):
            raise ValueError("Linux capabilities are only supported for EC2-backed ECS tasks")

        if app_config.compute_mode == "ec2":
            # Omit the task-level memory cap when every container carries its
            # own hard limit: ECS then reserves the sum of container-level
            # memory_reservation_mib (falling back to memory_limit_mib) for
            # placement instead of task-level memory. That's what makes two
            # hermes tasks share one node — container-level reservation < the
            # 4-GiB task-level value we'd otherwise advertise.
            all_have_hard_cap = all(c.memory_limit_mib is not None for c in app_config.containers)
            task_memory_mib = None if all_have_hard_cap else str(app_config.memory)
            task_definition = ecs.TaskDefinition(
                self, "TaskDefinition",
                compatibility=ecs.Compatibility.EC2,
                network_mode=ecs.NetworkMode.AWS_VPC,
                family=resource_prefix[:255],
                cpu=str(app_config.cpu),
                memory_mib=task_memory_mib,
                execution_role=self.environment_infra.task_execution_role,
                task_role=task_role,
            )
        elif app_config.compute_mode == "fargate":
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
        else:
            raise ValueError(f"Unsupported compute_mode: {app_config.compute_mode}")

        for mount_name, (access_point, volume_name) in efs_mount_resources.items():
            task_definition.add_volume(
                name=volume_name,
                efs_volume_configuration=ecs.EfsVolumeConfiguration(
                    file_system_id=self.environment_infra.efs_file_system_id,
                    transit_encryption="ENABLED",
                    authorization_config=ecs.AuthorizationConfig(
                        access_point_id=access_point.access_point_id,
                        iam="ENABLED",
                    ),
                ),
            )

        host_mount_resources: dict[tuple[str, int], str] = {}
        for c in app_config.containers:
            for mount_idx, mount in enumerate(c.host_mounts):
                if not Path(mount.source_path).is_absolute():
                    raise ValueError(
                        f"Container '{c.name}' host mount source_path must be absolute: {mount.source_path!r}"
                    )
                if ".." in Path(mount.source_path).parts:
                    raise ValueError(
                        f"Container '{c.name}' host mount source_path must not contain '..': {mount.source_path!r}"
                    )
                if not Path(mount.container_path).is_absolute():
                    raise ValueError(
                        f"Container '{c.name}' host mount container_path must be absolute: {mount.container_path!r}"
                    )
                if ".." in Path(mount.container_path).parts:
                    raise ValueError(
                        f"Container '{c.name}' host mount container_path must not contain '..': {mount.container_path!r}"
                    )
                volume_name = f"app-host-{c.name}-{mount_idx}"
                task_definition.add_volume(
                    name=volume_name,
                    host=ecs.Host(source_path=mount.source_path),
                )
                host_mount_resources[(c.name, mount_idx)] = volume_name

        # Two platform overlays, computed once so the main container loop stays uniform:
        #
        # 1. Env-bearer overlay — applied to every container with c.requires_env_bearer=True.
        #    Provides DOH_ENV_BEARER (from shared-secrets), DOH_ENV_SLUG,
        #    DOH_CONTROL_PLANE_URL, and DOH_OWNER_USERNAME when the app has
        #    an owner tag. Any env-resident component that calls DOH's
        #    control plane gets this.
        # 2. Policy-proxy-specific overlay — applied only to the policy-proxy
        #    container. Carries JWT verification URL, upstream wiring, etc.
        env_bearer_environment_overlay: dict[str, str] = {}
        env_bearer_secret_overlay: dict[str, ecs.Secret] = {}
        if app_config.needs_env_bearer():
            env_bearer_shared_secret = secretsmanager.Secret.from_secret_complete_arn(
                self, "EnvBearerSharedSecret", env_bearer_shared_secrets_arn,
            )
            env_bearer_environment_overlay = {
                "DOH_ENV_SLUG": env_slug,
                "DOH_CONTROL_PLANE_URL": _resolve_control_plane_url(),
            }
            if app_config.owner_username:
                env_bearer_environment_overlay["DOH_OWNER_USERNAME"] = app_config.owner_username
            env_bearer_secret_overlay = {
                "DOH_ENV_BEARER": ecs.Secret.from_secrets_manager(
                    env_bearer_shared_secret, field="DOH_ENV_BEARER",
                ),
            }

        policy_proxy_environment_overlay: dict[str, str] = {}
        if policy_proxy is not None:
            assert upstream is not None  # enforced above
            policy_proxy_environment_overlay = {
                "DOH_APP_ID": app_config.app_name,
                "DOH_ENV_DOMAIN": shared_alb_hosted_zone or "",
                "DOH_AUTH_BASE_URL": auth_base_url,
                "DOH_JWKS_URL": f"{auth_base_url.rstrip('/')}/.well-known/jwks.json",
                "DOH_PDP_URL": _resolve_pdp_url(),
                "DOH_UPSTREAM_HOST": "127.0.0.1",
                "DOH_UPSTREAM_PORT": str(upstream.container_port),
                "DOH_LISTEN_PORT": str(policy_proxy.container_port),
            }

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

            # Environment: per-container list of {name, value}, plus the
            # env-bearer overlay for every container that opts in, plus the
            # policy-proxy-specific overlay on the policy-proxy container.
            environment = {e["name"]: e["value"] for e in c.environment_variables}
            if c.requires_env_bearer:
                environment.update(env_bearer_environment_overlay)
            if c.image_source == appconfig.ImageSource.POLICY_PROXY:
                environment.update(policy_proxy_environment_overlay)

            # Secrets: the container's declared fields from the shared app_secrets bag,
            # plus database_connection_secret pieces on the ALB-target container only,
            # plus DOH_ENV_BEARER on any container that opts in.
            secrets: dict[str, ecs.Secret] = {}
            if app_secret_resource is not None and c.app_secrets:
                for field_name in c.app_secrets:
                    secrets[field_name] = ecs.Secret.from_secrets_manager(app_secret_resource, field=field_name)
            if c.name == alb_target.name:
                secrets.update(alb_target_database_secrets)
            if c.requires_env_bearer:
                secrets.update(env_bearer_secret_overlay)

            health_check = None
            if c.health_check_command:
                health_check = ecs.HealthCheck(
                    command=["CMD-SHELL", c.health_check_command],
                    interval=Duration.seconds(30),
                    timeout=Duration.seconds(10),
                    retries=3,
                    start_period=Duration.seconds(c.health_check_grace_period or 60),
                )

            linux_parameters = None
            if c.linux_capabilities:
                linux_parameters = ecs.LinuxParameters(self, f"LinuxParameters{idx}")
                for capability_name in c.linux_capabilities:
                    try:
                        capability = ecs.Capability[capability_name]
                    except KeyError as exc:
                        raise ValueError(
                            f"Container '{c.name}' requests unsupported Linux capability '{capability_name}'"
                        ) from exc
                    linux_parameters.add_capabilities(capability)

            container = task_definition.add_container(
                f"Container{idx}",
                container_name=f"{app_config.app_name}-{c.name}",
                image=ecs.ContainerImage.from_registry(image_uri),
                command=c.command,
                essential=c.essential,
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix=f"{app_config.app_name}-{c.name}",
                    log_group=self.environment_infra.log_group,
                ),
                environment=environment or None,
                secrets=secrets if secrets else None,
                health_check=health_check,
                linux_parameters=linux_parameters,
                user=c.user,
                privileged=c.privileged or None,
                stop_timeout=Duration.seconds(c.stop_timeout) if c.stop_timeout else None,
                memory_limit_mib=c.memory_limit_mib,
                memory_reservation_mib=c.memory_reservation_mib,
            )
            # Only the ALB-target container needs a port mapping visible to ECS
            # task-networking — sibling containers communicate over the task's
            # shared loopback where no mapping is required.
            if c.name == alb_target.name:
                container.add_port_mappings(
                    ecs.PortMapping(container_port=c.container_port, protocol=ecs.Protocol.TCP),
                )

            for mount_name in c.efs_mounts:
                if mount_name not in efs_mount_resources:
                    raise ValueError(
                        f"Container '{c.name}' requests EFS mount '{mount_name}' not declared in efs_config.mounts"
                    )
                assert app_config.efs_config is not None  # efs_mount_resources is non-empty only when set
                mount_spec = app_config.efs_config.by_name(mount_name)
                _, volume_name = efs_mount_resources[mount_name]
                container.add_mount_points(
                    ecs.MountPoint(
                        container_path=mount_spec.container_path,
                        source_volume=volume_name,
                        read_only=False,
                    ),
                )

            for mount_idx, mount in enumerate(c.host_mounts):
                container.add_mount_points(
                    ecs.MountPoint(
                        container_path=mount.container_path,
                        source_volume=host_mount_resources[(c.name, mount_idx)],
                        read_only=False,
                    ),
                )

            containers_by_name[c.name] = container

        for c in app_config.containers:
            if not c.depends_on:
                continue
            cdefn = containers_by_name[c.name]
            for dep in c.depends_on:
                if dep.name not in containers_by_name:
                    raise ValueError(f"Container '{c.name}' depends_on unknown container '{dep.name}'")
                cdefn.add_container_dependencies(
                    ecs.ContainerDependency(
                        container=containers_by_name[dep.name],
                        condition=_container_dependency_condition(cond=dep.condition),
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

        # When the policy proxy is the ALB target, the ALB's health check must
        # hit a route the proxy handles locally (bypassing the PDP), otherwise
        # ALB probes would all 302 to auth and never go healthy. The proxy
        # exposes /__policy_proxy/healthz for exactly this.
        if policy_proxy is not None:
            target_health_check_path = "/__policy_proxy/healthz"
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
        #
        # max_healthy_percent=100 when the template asks for it: ECS will not
        # start the replacement task until the old one is fully stopped. This
        # trades deploy/node-move downtime for correctness on checkpoint-based
        # apps (Hermes writes its EFS checkpoint during SIGTERM; overlapping
        # starts race the write and the new task boots from image, dropping
        # conversation state). ECS rejects max<=100 with AZ Rebalancing on, so
        # we disable it in the same branch — acceptable for single-task
        # services, where "rebalanced across AZs" doesn't apply.
        if app_config.serialize_task_replacement:
            max_healthy = 100
            az_rebalancing = ecs.AvailabilityZoneRebalancing.DISABLED
        else:
            max_healthy = 200
            az_rebalancing = ecs.AvailabilityZoneRebalancing.ENABLED
        service_props = {
            "service_name": resource_prefix[:255],
            "cluster": self.environment_infra.cluster,
            "task_definition": task_definition,
            "desired_count": 1,
            "assign_public_ip": False,
            "vpc_subnets": ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            "security_groups": [self.environment_infra.default_security_group],
            "enable_execute_command": True,
            "min_healthy_percent": min_healthy,
            "max_healthy_percent": max_healthy,
            "availability_zone_rebalancing": az_rebalancing,
            "health_check_grace_period": Duration.seconds(health_check_grace),
            "circuit_breaker": ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
        }
        if app_config.compute_mode == "ec2":
            service = ecs.Ec2Service(
                self, "EcsService",
                capacity_provider_strategies=[
                    ecs.CapacityProviderStrategy(
                        capacity_provider=self.environment_infra.ec2_capacity_provider_name,
                        weight=1,
                    ),
                ],
                **service_props,
            )
        else:
            service = ecs.FargateService(
                self, "EcsService",
                **service_props,
            )
        # Multiple containers in the task — be explicit about which one the
        # ALB targets. alb_target_container already names it (the policy
        # proxy when one is declared, the app container otherwise).
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
    if app_config.compute_mode == "ec2" and not _environment_has_ec2_capacity_provider(session=session, env_slug=env_slug):
        capacity_provider_name = deploy_base.ec2_capacity_provider_name(env_slug=env_slug)
        msg = (
            f"EC2 compute requested, but capacity provider '{capacity_provider_name}' is not associated with "
            f"cluster 'devopshero-{env_slug}-cluster'. Redeploy the environment base infrastructure first."
        )
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

    # Env-bearer prerequisite: shared-secrets entry + EnvironmentBearerToken row.
    # Any container in the app that opts into the DOH control-plane bearer
    # needs this. Idempotent; reused across apps sharing the env.
    policy_proxy_needed = app_config.policy_proxy_container() is not None
    env_bearer_needed = app_config.needs_env_bearer()
    env_bearer_shared_secrets_arn: str | None = None

    from devopshero_app.models import Environment
    env_obj = Environment.objects.get(slug=env_slug)

    if env_bearer_needed:
        logger.info("Ensuring per-env bearer token exists")
        env_bearer_shared_secrets_arn = secrets_utils.ensure_env_bearer_token_exists(
            session=session, env=env_obj,
        )

    # Policy-proxy prerequisites: additionally, the per-env auth-config secret +
    # ECR repo + image push + auth Lambda. All idempotent, safe to run on every
    # policy-proxy deploy. The first policy-proxy deploy in an env does the
    # heavy lift; subsequent deploys are fast because the secrets, stacks, and
    # image already exist.
    policy_proxy_secret_arns: dict[str, str] = {}
    policy_proxy_auth_base_url: str | None = None
    if policy_proxy_needed:
        if not shared_alb_hosted_zone or not shared_hosted_zone_id:
            msg = "Policy-proxy apps require a hosted zone (HTTPS)"
            logger.error(msg)
            return DeployResult(success=False, error=msg, service_url="", alb_dns="")

        logger.info("Ensuring per-env policy-proxy infrastructure exists")
        policy_proxy_secret_arns = secrets_utils.ensure_env_policy_proxy_secrets_exist(
            session=session, env=env_obj,
        )
        policy_proxy_auth_base_url = f"https://auth.{shared_alb_hosted_zone}"

    cdk_app = App(outdir=str(cdk_utils.CDK_OUT_DIR))

    ecr_stack = EcrStack(
        cdk_app,
        f"{resource_prefix}-ecr",
        app_config=app_config,
        resource_prefix=resource_prefix,
    )

    policy_proxy_ecr_stack = None
    auth_service_stack = None
    if policy_proxy_needed:
        policy_proxy_ecr_stack = PolicyProxyEcrStack(
            cdk_app,
            f"devopshero-{env_slug}-policy-proxy-ecr",
            env_slug=env_slug,
        )
        # Image URI is deterministic from account+region+env+version, so we
        # can compute it at synth time even though the image is pushed later.
        policy_proxy_image_uri = (
            f"{account_id}.dkr.ecr.{region}.amazonaws.com/"
            f"{policy_proxy_ecr_repo_name(env_slug)}:{POLICY_PROXY_IMAGE_VERSION}"
        )
        auth_service_stack = auth_service.AuthServiceStack(
            cdk_app,
            f"devopshero-{env_slug}-auth-service",
            inputs=auth_service.AuthServiceInputs(
                env_slug=env_slug,
                env_domain=shared_alb_hosted_zone,
                policy_proxy_image_uri=policy_proxy_image_uri,
                policy_proxy_auth_config_secret_arn=policy_proxy_secret_arns["policy_proxy_auth_config_arn"],
                shared_alb_https_listener_arn=Fn.import_value(
                    f"devopshero-{env_slug}-shared-alb-https-listener-arn",
                ),
                shared_alb_security_group_id=Fn.import_value(
                    f"devopshero-{env_slug}-shared-alb-sg-id",
                ),
                shared_hosted_zone_id=shared_hosted_zone_id,
                shared_hosted_zone_name=shared_alb_hosted_zone,
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
        env_bearer_shared_secrets_arn=env_bearer_shared_secrets_arn,
        auth_base_url=policy_proxy_auth_base_url,
    )
    app_stack.add_dependency(ecr_stack)
    if aurora_stack:
        app_stack.add_dependency(aurora_stack)

    assembly_dir = cdk_utils.synth_cdk_app(cdk_app)

    if synth_only:
        return DeployResult(success=True, error="", service_url="", alb_dns="")

    # Phase 1a: Policy-proxy infra for policy-proxy apps only.
    # The auth service runs the policy-proxy image, so the image must exist in
    # ECR before the auth-service stack starts a task from it. Order:
    #   1. Deploy the per-env policy-proxy ECR repo stack.
    #   2. Build + push the policy-proxy image at POLICY_PROXY_IMAGE_VERSION.
    #   3. Deploy the auth-service stack (its Fargate task pulls that image).
    if policy_proxy_needed:
        if not cdk_utils.deploy_from_assembly(
            assembly_dir=assembly_dir, session=session,
            stack_names=[f"devopshero-{env_slug}-policy-proxy-ecr"],
        ):
            logger.error("CDK deployment failed (policy-proxy-ecr)")
            return DeployResult(success=False, error="CDK deployment failed (policy-proxy-ecr)", service_url="", alb_dns="")

        # Push the DOH-owned policy-proxy image into the per-env repo. This is
        # idempotent: if the tag already exists in ECR the push is a no-op.
        logger.info("Building and pushing policy-proxy image (%s)", POLICY_PROXY_IMAGE_VERSION)
        policy_proxy_image_uri = ecr_utils.build_and_push_docker_image(
            session=session,
            account_id=account_id,
            region=region,
            env_slug=env_slug,
            app_name="policy-proxy",
            ecr_repo_name=policy_proxy_ecr_repo_name(env_slug),
            app_source_path=POLICY_PROXY_SOURCE_DIR,
            image_tag=POLICY_PROXY_IMAGE_VERSION,
        )
        if not policy_proxy_image_uri:
            logger.error("Policy-proxy image build/push failed")
            return DeployResult(success=False, error="Policy-proxy image build/push failed", service_url="", alb_dns="")

        if not cdk_utils.deploy_from_assembly(
            assembly_dir=assembly_dir, session=session,
            stack_names=[f"devopshero-{env_slug}-auth-service"],
        ):
            logger.error("CDK deployment failed (auth-service)")
            return DeployResult(success=False, error="CDK deployment failed (auth-service)", service_url="", alb_dns="")

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
    env_slug: str,
    app_name: str,
    has_database: bool,
    dockerfile_ecr_repo_names: list[str],
) -> bool:
    """
    Delete app-specific CDK stacks (ECR, ALB, ECS service, Aurora if applicable).

    Prebuilt-container repos are per-env shared resources and are not torn
    down here — only the per-app dockerfile ECR repos get emptied.
    """
    cf_client = session.client("cloudformation")

    resource_prefix = f"doh-{env_slug}-{app_name}"

    # App-specific stacks in reverse dependency order
    stacks_to_delete = [f"{resource_prefix}-app"]
    if has_database:
        stacks_to_delete.append(f"{resource_prefix}-aurora")
    stacks_to_delete.append(f"{resource_prefix}-ecr")

    logger.info("Tearing down app: %(app_name)s", {"app_name": app_name})
    logger.info("Stacks to delete (in order):")
    for stack in stacks_to_delete:
        logger.info("   - %(stack_name)s", {"stack_name": stack})

    # CloudFormation can't delete non-empty ECR repos.
    for repo_name in dockerfile_ecr_repo_names:
        ecr_utils.delete_all_ecr_images(session=session, ecr_repo_name=repo_name)

    all_success = True
    for stack_name in stacks_to_delete:
        success = cloudformation_utils.delete_stack_and_wait(cf_client, stack_name=stack_name)
        if not success:
            all_success = False

    if all_success:
        logger.info("App '%(app_name)s' stacks deleted successfully", {"app_name": app_name})
        return all_success

    logger.error("Some stacks failed to delete")

    return all_success
