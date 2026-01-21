"""
Build AppConfig from Django models.

Converts a Django App model (with related Workspace, Datastore, Environment)
into an appconfig.AppConfig suitable for CDK deployment.
"""

from pathlib import Path

from devopshero_app.models import App, Datastore, Environment
from devopshero_app.services import infra_customer


def derive_hosted_zone_name(domain_name: str | None) -> str | None:
    """Derive hosted zone name from domain name (e.g., 'foo.example.com' -> 'example.com')."""
    if not domain_name:
        return None
    parts = domain_name.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return None


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
        app: The Django App model with related workspace and datastore.
        environment: The target Environment for deployment.

    Returns:
        An AppConfig ready for CDK deployment.
    """
    workspace = app.workspace

    # Build ECR repo name (app slugs are globally unique, so no workspace prefix needed)
    ecr_repo_name = f"doh/{environment.slug}/{app.slug}"

    # Extract app source path from workspace repo URL
    app_source_path = extract_repo_path(workspace.primary_repo_url)

    # Derive hosted zone from domain name
    hosted_zone_name = derive_hosted_zone_name(app.domain_name)

    # Build database config if app has a datastore
    database_config = None
    if app.datastore:
        database_config = build_database_config(app.datastore)

    return infra_customer.appconfig.AppConfig(
        app_name=app.slug,  # This is a huge decision. For example, app_name is used to derive the secrets path in
                            # secrets_utils.py (e.g., "devopshero/{app_name}/secrets"). But apps may have
                            # hardcoded expectations about their secret path. If the slug doesn't match
                            # (e.g., agent creates "db-portal" twice → second gets "db-portal-1"), the app
                            # can't find its secrets. Consider separating app_name (for resource naming) from secrets_path,
                            # and the agent repository-analyzer determines the secret path by reading the code.
                            # beads: devopshero-bxr
        ecr_repo_name=ecr_repo_name,
        container_port=app.container_port,
        cpu=app.cpu,
        memory=app.memory,
        health_check_path=app.health_check_path,
        health_check_command=app.health_check_command or None,
        environment_variables=app.environment_variables or [],
        app_source_path=app_source_path,
        domain_name=app.domain_name or None,
        hosted_zone_name=hosted_zone_name,
        database_config=database_config,
        app_secrets=app.app_secrets,
    )
