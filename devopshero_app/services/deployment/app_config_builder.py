"""
Build AppConfig from Django models.

Converts a Django App model (with related Workspace, Datastore, Environment)
into an appconfig.AppConfig suitable for CDK deployment.
"""

from pathlib import Path

from devopshero_app.models import App, Datastore, Environment
from devopshero_app.services import infra_customer


def extract_repo_path(repo_url: str) -> Path | None:
    """Extract local path from file:// URL."""
    if repo_url.startswith("file://"):
        return Path(repo_url[7:])
    return None


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


def build_app_config(app: App, environment: Environment) -> infra_customer.appconfig.AppConfig:
    """
    Build an AppConfig from Django App model.

    Args:
        app: The Django App model with related repository and datastore.
        environment: The target Environment for deployment.

    Returns:
        An AppConfig ready for CDK deployment.
    """
    # Build ECR repo name (app slugs are unique per org, environments are per-account, no collision)
    ecr_repo_name = f"doh/{environment.slug}/{app.slug}"

    # Extract app source path from repository clone URL
    app_source_path = extract_repo_path(app.repository.clone_url)
    if app_source_path and app.repo_subpath:
        app_source_path = app_source_path / app.repo_subpath

    # Build database config if app has a datastore
    database_config = None
    if app.datastore:
        database_config = build_database_config(app.datastore)

    return infra_customer.appconfig.AppConfig(
        app_name=app.slug,
        ecr_repo_name=ecr_repo_name,
        container_port=app.container_port,
        cpu=app.cpu,
        memory=app.memory,
        health_check_path=app.health_check_path,
        health_check_command=app.health_check_command or None,
        environment_variables=app.environment_variables or [],
        app_source_path=app_source_path,
        database_config=database_config,
        app_secrets=app.app_secrets,
    )
