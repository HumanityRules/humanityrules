"""
Deploy DevOpsHero apps (ECR, ALB, ECS service) using AWS CDK.
"""

import logging

import boto3
from aws_cdk import App, Aws, CfnOutput, Duration, Fn, RemovalPolicy, SecretValue, Stack, Tags
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from . import appconfig
from . import cdk_utils
from . import cloudformation_utils
from . import deploy_base
from . import ecr_utils
from . import ecs_utils
from . import secrets_utils

logger = logging.getLogger(__name__)


# =============================================================================
# CDK STACKS
# =============================================================================

AURORA_MYSQL_DEFAULT_VERSION = "3.04.0"
AURORA_POSTGRES_DEFAULT_VERSION = "15.4"

AURORA_MYSQL_VERSION_MAP = {
    "3.04.0": rds.AuroraMysqlEngineVersion.VER_3_04_0,
}

AURORA_POSTGRES_VERSION_MAP = {
    "15.4": rds.AuroraPostgresEngineVersion.VER_15_4,
}


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
    if engine_config.family == "aurora-mysql":
        version_key = engine_config.version or AURORA_MYSQL_DEFAULT_VERSION
        version = AURORA_MYSQL_VERSION_MAP.get(version_key)
        if not version:
            raise ValueError(f"Unsupported Aurora MySQL version: {version_key}")
        return rds.DatabaseClusterEngine.aurora_mysql(version=version)
    if engine_config.family == "aurora-postgresql":
        version_key = engine_config.version or AURORA_POSTGRES_DEFAULT_VERSION
        version = AURORA_POSTGRES_VERSION_MAP.get(version_key)
        if not version:
            raise ValueError(f"Unsupported Aurora PostgreSQL version: {version_key}")
        return rds.DatabaseClusterEngine.aurora_postgres(version=version)
    raise ValueError(f"Unsupported engine family: {engine_config.family}")


def get_connection_env_var_name(connection_config: appconfig.ConnectionConfig) -> str:
    return connection_config.env_var_name or "DATABASE_URL"


class EcrStack(Stack):
    """
    DevOpsHero ECR Stack - Container Registry for the app.
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

        self.repository = ecr.Repository(
            self, "EcrRepository",
            repository_name=app_config.ecr_repo_name,
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            lifecycle_rules=[ecr.LifecycleRule(description="Keep last 10 images", max_image_count=10, rule_priority=1)],
        )
        Tags.of(self.repository).add("App", app_config.app_name)

        CfnOutput(self, "EcrRepositoryUri", value=self.repository.repository_uri, export_name=f"{resource_prefix}-ecr-uri")
        CfnOutput(self, "EcrRepositoryArn", value=self.repository.repository_arn, export_name=f"{resource_prefix}-ecr-arn")


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
        secret_name = f"devopshero/{app_config.app_name}/aurora/credentials"

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

        connection_secret_name = f"devopshero/{app_config.app_name}/aurora/connection"
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
        database_connection_secret: secretsmanager.ISecret | None,
        shared_alb_hosted_zone: str | None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

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
                resources=[f"arn:aws:secretsmanager:{Aws.REGION}:{Aws.ACCOUNT_ID}:secret:devopshero/{app_config.app_name}/*"],
            ))
        if database_connection_secret:
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[database_connection_secret.secret_arn],
            ))

        environment = {env["name"]: env["value"] for env in app_config.environment_variables}

        # If database connection secret is provided, inject DB credentials from Secrets Manager
        secrets = {}
        if database_connection_secret and app_config.database_config:
            env_var_name = get_connection_env_var_name(app_config.database_config.connection)
            secrets[env_var_name] = ecs.Secret.from_secrets_manager(database_connection_secret, field="url")
            secrets["DATABASE_HOST"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="host")
            secrets["DATABASE_PORT"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="port")
            secrets["DATABASE_NAME"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="dbname")
            secrets["DATABASE_USERNAME"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="username")
            secrets["DATABASE_PASSWORD"] = ecs.Secret.from_secrets_manager(database_connection_secret, field="password")

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

        container = task_definition.add_container(
            "AppContainer",
            container_name=app_config.app_name,
            image=ecs.ContainerImage.from_registry(f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/{app_config.ecr_repo_name}:{image_tag}"),
            logging=ecs.LogDrivers.aws_logs(stream_prefix=app_config.app_name, log_group=self.environment_infra.log_group),
            environment=environment,
            secrets=secrets if secrets else None,
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", app_config.health_check_command],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(60),
            ) if app_config.health_check_command else None,
        )
        container.add_port_mappings(ecs.PortMapping(container_port=app_config.container_port, protocol=ecs.Protocol.TCP))

        # TODO(production): Increase deregistration_delay for graceful connection draining,
        # and consider higher health check interval/timeout for stability.
        # Current aggressive settings optimize for fast dev iteration.
        target_group = elbv2.ApplicationTargetGroup(
            self, "TargetGroup",
            target_group_name=f"doh-{env_slug}-{app_config.app_name}"[:32],
            vpc=self.environment_infra.vpc,
            port=app_config.container_port,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            deregistration_delay=Duration.seconds(5),
            health_check=elbv2.HealthCheck(
                enabled=True, path=app_config.health_check_path, protocol=elbv2.Protocol.HTTP,
                interval=Duration.seconds(5), timeout=Duration.seconds(2),
                healthy_threshold_count=2, unhealthy_threshold_count=3, healthy_http_codes="200",
            ),
        )

        # Configure routing rules on the shared ALB
        self._setup_shared_alb_routing(
            app_config=app_config,
            env_slug=env_slug,
            resource_prefix=resource_prefix,
            target_group=target_group,
            shared_alb_hosted_zone=shared_alb_hosted_zone,
        )

        service = ecs.FargateService(
            self, "EcsService",
            service_name=resource_prefix[:255],
            cluster=self.environment_infra.cluster,
            task_definition=task_definition,
            desired_count=0,  # Start at 0, scaled up after image push
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[self.environment_infra.default_security_group],
            enable_execute_command=True,
            min_healthy_percent=100,  # Zero-downtime: keep old task until new one is healthy
            max_healthy_percent=200,
        )
        service.attach_to_application_target_group(target_group)

        Tags.of(service).add("App", app_config.app_name)
        Tags.of(task_definition).add("App", app_config.app_name)

        CfnOutput(self, "TaskDefinitionArn", value=task_definition.task_definition_arn, export_name=f"{resource_prefix}-task-def-arn")
        CfnOutput(self, "ServiceArn", value=service.service_arn, export_name=f"{resource_prefix}-service-arn")
        CfnOutput(self, "ServiceName", value=service.service_name, export_name=f"{resource_prefix}-service-name")

    def _setup_shared_alb_routing(
        self,
        app_config: appconfig.AppConfig,
        env_slug: str,
        resource_prefix: str,
        target_group: elbv2.ApplicationTargetGroup,
        shared_alb_hosted_zone: str | None,
    ) -> None:
        """Configure routing rules on the shared ALB."""
        priority = _compute_listener_rule_priority(app_config.app_name)
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
            # Use app slug + hosted zone for the hostname
            app_hostname = f"{app_config.app_name}.{shared_alb_hosted_zone}"
            host_condition = elbv2.ListenerCondition.host_headers([app_hostname])
        else:
            # HTTP-only mode: route by path prefix since no domain
            app_hostname = None
            host_condition = elbv2.ListenerCondition.path_patterns([f"/{app_config.app_name}/*"])

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
    synth_only: bool,
    shared_alb_hosted_zone: str | None,
) -> bool:
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
        synth_only: If True, only synthesize templates, don't deploy.
        shared_alb_hosted_zone: Hosted zone for shared ALB (e.g., "dev.example.com"). None = HTTP only.
    Returns:
        True on success, False on failure.
    """
    logger.info("Deploying app '%(app_name)s' to environment '%(env_slug)s'", {"app_name": app_config.app_name, "env_slug": env_slug})

    # Resource prefix for consistent naming: doh-{env}-{app} (app slugs are per organization unique)
    resource_prefix = f"doh-{env_slug}-{app_config.app_name}"

    # Verify infrastructure exists
    cf_client = session.client("cloudformation")
    vpc_stack_name = f"devopshero-{env_slug}-vpc"
    cluster_stack_name = f"devopshero-{env_slug}-cluster"

    if not cloudformation_utils.stack_exists(cf_client, vpc_stack_name):
        logger.error("Base layer not deployed. VPC stack '%(stack_name)s' not found", {"stack_name": vpc_stack_name})
        return False
    if not cloudformation_utils.stack_exists(cf_client, cluster_stack_name):
        logger.error("ECS cluster not deployed. Cluster stack '%(stack_name)s' not found", {"stack_name": cluster_stack_name})
        return False

    # Ensure app secrets exist in Secrets Manager (created outside CDK for security)
    if app_config.app_secrets:
        logger.info("Ensuring app secrets exist")
        secrets_utils.ensure_app_secrets_exist(session=session, app_config=app_config)

    cdk_app = App(outdir=str(cdk_utils.CDK_OUT_DIR))

    ecr_stack = EcrStack(
        cdk_app,
        f"{resource_prefix}-ecr",
        app_config=app_config,
        resource_prefix=resource_prefix,
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
        database_connection_secret=aurora_connection_secret,
        shared_alb_hosted_zone=shared_alb_hosted_zone,
    )
    app_stack.add_dependency(ecr_stack)
    if aurora_stack:
        app_stack.add_dependency(aurora_stack)

    if synth_only:
        cloud_assembly = cdk_app.synth()
        logger.info("CDK templates synthesized to: %(directory)s", {"directory": cloud_assembly.directory})
        return True

    logger.info("Deploying CDK stacks")
    success = cdk_utils.deploy_cdk_stacks(cdk_app, session)

    if not success:
        logger.error("CDK deployment failed")
        return False

    logger.info("Building and pushing Docker image")
    image_uri = ecr_utils.build_and_push_docker_image(
        session=session,
        account_id=account_id,
        region=region,
        env_slug=env_slug,
        app_name=app_config.app_name,
        ecr_repo_name=app_config.ecr_repo_name,
        app_source_path=app_config.app_source_path,
        image_tag=image_tag,
    )
    if not image_uri:
        logger.error("Docker build/push failed")
        return False

    logger.info("Starting ECS service")
    if not ecs_utils.start_ecs_service(
        session=session,
        service_name=resource_prefix,
        cluster_name=app_stack.environment_infra.cluster.cluster_name,
    ):
        logger.error("Failed to start ECS service")
        return False

    logger.info("Deployment of %(app_name)s completed successfully", {"app_name": app_config.app_name})

    cloudformation_utils.print_deployment_summary(
        cf_client=cf_client,
        account_id=account_id,
        region=region,
        app_name=app_config.app_name,
        env_slug=env_slug,
        image_tag=image_tag,
        has_domain=bool(shared_alb_hosted_zone),
        cluster_name=app_stack.environment_infra.cluster.cluster_name,
    )

    return True


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

    # Empty ECR repository first (CloudFormation can't delete non-empty repos)
    ecr_utils.delete_all_ecr_images(session=session, ecr_repo_name=app_config.ecr_repo_name)

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
