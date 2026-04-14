"""
Build AppConfig from Django models.

Converts a Django App model (with related Workspace, Datastore, Environment)
into an appconfig.AppConfig suitable for CDK deployment.
"""

from pathlib import Path

from devopshero_app.models import Datastore, DeploymentBlueprint
from devopshero_app.services import infra_customer


def build_database_config(datastore: Datastore) -> infra_customer.appconfig.DatabaseConfig:
    """Build DatabaseConfig from Django Datastore model."""
    # Engine config
    engine = infra_customer.appconfig.EngineConfig(
        family=datastore.engine,
        version=datastore.engine_version or None,
        auto_minor_version_upgrade=True,
    )

    # Deployment config
    if datastore.deployment_mode == Datastore.DeploymentMode.SERVERLESS_V2:
        deployment = infra_customer.appconfig.DeploymentConfig(
            mode="aurora_serverless_v2",
            serverless_v2=infra_customer.appconfig.ServerlessV2Config(
                min_acu=datastore.serverless_min_acu or 0.5,
                max_acu=datastore.serverless_max_acu or 2.0,
            ),
            provisioned=None,
        )
    else:
        deployment = infra_customer.appconfig.DeploymentConfig(
            mode="aurora_provisioned",
            serverless_v2=None,
            provisioned=infra_customer.appconfig.ProvisionedConfig(
                instance_class=datastore.provisioned_instance_class or "db.r6g.large",
            ),
        )

    return infra_customer.appconfig.DatabaseConfig(
        name=datastore.database_name,
        engine=engine,
        deployment=deployment,
        backups=infra_customer.appconfig.BackupConfig(
            retention_days=datastore.backup_retention_days,
            copy_tags_to_snapshot=True,
        ),
        security=infra_customer.appconfig.SecurityConfig(
            storage_encrypted=datastore.storage_encrypted,
            deletion_protection=datastore.deletion_protection,
        ),
        connection=infra_customer.appconfig.ConnectionConfig(
            env_var_name="DATABASE_URL",
        ),
    )


def build_app_config_from_blueprint(blueprint: DeploymentBlueprint, repo_path: Path) -> infra_customer.appconfig.AppConfig:
    """Build an AppConfig sourcing identity/build from App and runtime from DeploymentBlueprint."""
    app = blueprint.app
    environment = blueprint.environment

    ecr_repo_name = f"doh/{environment.slug}/{app.slug}"

    app_source_path = repo_path
    if app.repo_subpath:
        app_source_path = repo_path / app.repo_subpath

    database_config = None
    if blueprint.datastore:
        database_config = build_database_config(blueprint.datastore)

    cdk_stack_profile = "fargate_web"
    if app.source_template:
        cdk_stack_profile = app.source_template.cdk_stack_profile

    return infra_customer.appconfig.AppConfig(
        app_name=app.slug,
        ecr_repo_name=ecr_repo_name,
        container_port=app.container_port,
        cpu=blueprint.cpu,
        memory=blueprint.memory,
        health_check_path=app.health_check_path,
        health_check_command=app.health_check_command or None,
        environment_variables=blueprint.environment_variables or [],
        health_check_grace_period=app.health_check_grace_period or None,
        app_source_path=app_source_path,
        database_config=database_config,
        app_secrets=blueprint.app_secrets,
        cdk_stack_profile=cdk_stack_profile,
    )
