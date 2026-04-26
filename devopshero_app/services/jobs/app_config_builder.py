"""
Build AppConfig from Django models.

Converts a Django App model (with related Workspace, Datastore, Environment)
into an appconfig.AppConfig suitable for CDK deployment.
"""

from pathlib import Path

from devopshero_app.models import AppTemplate, Datastore, DeploymentBlueprint
from devopshero_app.services import infra_customer
from devopshero_app.services.app_templates import template_deploy_service
from devopshero_app.services.infra_customer.appconfig import (
    AppConfig,
    ContainerConfig,
    ContainerDependencyConfig,
)


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


def _merge_container_environment(
    template_container: dict, blueprint_container: dict,
) -> list[dict[str, str]]:
    """Merge materialized template runtime_variables with blueprint environment (blueprint wins on name)."""
    t_list = template_deploy_service._materialize_environment_variables(
        template_container.get("runtime_variables", []),
    )
    merged: dict[str, str] = {e["name"]: e["value"] for e in t_list}
    for e in blueprint_container.get("environment_variables") or []:
        merged[e["name"]] = e["value"]
    return [{"name": name, "value": value} for name, value in merged.items()]


class ContainerSecretCollision(ValueError):
    """Two containers declared the same secret field name with different values."""


def _union_app_secrets(containers: list[ContainerConfig]) -> dict[str, str | None]:
    """Collision-checked union of each container's app_secrets.

    Same field name across containers must declare identical values (literal,
    ""-placeholder, or None-auto-generate all compared by equality).
    """
    merged: dict[str, str | None] = {}
    owners: dict[str, str] = {}
    for c in containers:
        for field_name, value in c.app_secrets.items():
            if field_name in merged and merged[field_name] != value:
                raise ContainerSecretCollision(
                    f"Secret '{field_name}' declared with different values in "
                    f"containers '{owners[field_name]}' and '{c.name}': "
                    f"{merged[field_name]!r} vs {value!r}"
                )
            merged[field_name] = value
            owners.setdefault(field_name, c.name)
    return merged


def _build_container_config(
    template_container: dict,
    blueprint_container: dict,
    app_name: str,
    env_slug: str,
) -> ContainerConfig:
    """Project a (template, blueprint) container pair into a ContainerConfig."""
    name = template_container["name"]
    image_source = template_container["image_source"]

    command = template_container.get("command")
    common = dict(
        name=name,
        image_source=image_source,
        container_port=template_container["container_port"],
        health_check_path=template_container.get("health_check_path") or None,
        health_check_command=template_container.get("health_check_command") or None,
        health_check_grace_period=template_container.get("health_check_grace_period") or None,
        environment_variables=_merge_container_environment(
            template_container=template_container, blueprint_container=blueprint_container,
        ),
        app_secrets=dict(blueprint_container.get("app_secrets") or {}),
        efs_mount=bool(template_container.get("efs_mount", False)),
        efs_docker_workspace_only=bool(template_container.get("efs_docker_workspace_only", False)),
        efs_docker_workspace_container_path=template_container.get("efs_docker_workspace_container_path")
        or None,
        privileged=bool(template_container.get("privileged", False)),
        depends_on=[
            ContainerDependencyConfig(
                name=dep["name"],
                condition=dep["condition"],
            )
            for dep in template_container.get("depends_on", [])
        ],
        user=template_container.get("user") or None,
        command=list(command) if command else None,
        essential=bool(template_container.get("essential", True)),
    )

    if image_source == "dockerfile":
        return ContainerConfig(
            **common,
            source_repo_path=template_container["source_repo_path"],
            dockerfile_path=template_container.get("dockerfile_path") or None,
            ecr_repo_name=f"doh/{env_slug}/{app_name}-{name}",
        )
    if image_source == "prebuilt":
        return ContainerConfig(
            **common,
            prebuilt_ecr_repo=template_container["ecr_repo"],
            prebuilt_version=template_container["version"],
        )
    if image_source == "registry":
        if not template_container.get("registry_image"):
            msg = f"Container '{name}' is image_source=registry but has no registry_image"
            raise ValueError(msg)
        return ContainerConfig(
            **common,
            registry_image=template_container["registry_image"],
        )
    raise ValueError(f"Unknown image_source='{image_source}' on container '{name}'")


def _match_blueprint_containers_to_template(
    template_containers: list[dict],
    blueprint_containers: list[dict],
) -> dict[str, dict]:
    """Return a {name: blueprint_container_dict} lookup, falling back to empty for missing names."""
    by_name = {c["name"]: c for c in blueprint_containers}
    return {c["name"]: by_name.get(c["name"], {"name": c["name"]}) for c in template_containers}


def build_app_config_from_blueprint(blueprint: DeploymentBlueprint, repo_path: Path) -> AppConfig:
    """Build an AppConfig sourcing identity/build from App + template and runtime from DeploymentBlueprint."""
    app = blueprint.app
    environment = blueprint.environment
    template: AppTemplate | None = app.source_template

    if template is None or not template.containers:
        raise ValueError(
            f"App '{app.slug}' has no source_template with containers; "
            f"multi-container deploy path requires a template-backed app."
        )

    app_source_path = repo_path
    if app.repo_subpath:
        app_source_path = repo_path / app.repo_subpath

    database_config = None
    if blueprint.datastore:
        database_config = build_database_config(blueprint.datastore)

    efs_config = None
    if template.efs_config:
        raw = template.efs_config
        efs_config = infra_customer.appconfig.EfsConfig(
            mount_path=raw["mount_path"],
            posix_uid=raw["posix_uid"],
            posix_gid=raw["posix_gid"],
            docker_workspace_subpath=raw.get("docker_workspace_subpath"),
        )

    blueprint_by_name = _match_blueprint_containers_to_template(
        template_containers=template.containers,
        blueprint_containers=blueprint.containers or [],
    )

    containers = [
        _build_container_config(
            template_container=tc,
            blueprint_container=blueprint_by_name[tc["name"]],
            app_name=app.slug,
            env_slug=environment.slug,
        )
        for tc in template.containers
    ]

    app_secrets_union = _union_app_secrets(containers)

    return AppConfig(
        app_name=app.slug,
        cpu=blueprint.cpu,
        memory=blueprint.memory,
        containers=containers,
        compute_mode=blueprint.compute_mode,
        app_source_path=app_source_path,
        alb_target_container=template.alb_target_container,
        database_config=database_config,
        app_secrets=app_secrets_union or None,
        efs_config=efs_config,
        sidecar_enabled=bool(template.sidecar_enabled),
        platform_capabilities=list(template.platform_capabilities or []),
    )
