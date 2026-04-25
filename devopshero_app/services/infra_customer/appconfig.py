"""
Application configuration dataclass for ECS deployments.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


ComputeMode = Literal["fargate", "ec2"]


@dataclass
class EngineConfig:
    """Aurora engine configuration."""
    family: str  # "aurora-mysql" or "aurora-postgresql"
    version: str | None  # Version catalog key, None = default
    auto_minor_version_upgrade: bool


@dataclass
class ServerlessV2Config:
    """Aurora Serverless v2 scaling configuration."""
    min_acu: float
    max_acu: float


@dataclass
class ProvisionedConfig:
    """Aurora provisioned instance configuration."""
    instance_class: str  # e.g., "db.r6g.large"


@dataclass
class DeploymentConfig:
    """Aurora deployment mode configuration."""
    mode: str  # "aurora_serverless_v2" or "aurora_provisioned"
    serverless_v2: ServerlessV2Config | None
    provisioned: ProvisionedConfig | None


@dataclass
class BackupConfig:
    """Backup configuration."""
    retention_days: int
    copy_tags_to_snapshot: bool


@dataclass
class SecurityConfig:
    """Security settings."""
    storage_encrypted: bool
    deletion_protection: bool


@dataclass
class ConnectionConfig:
    """Connection injection settings."""
    env_var_name: str | None


@dataclass
class EfsConfig:
    """EFS volume configuration for the task (one volume, shared across containers that opt in)."""
    mount_path: str
    posix_uid: int
    posix_gid: int


@dataclass
class DatabaseConfig:
    """Configuration for an Aurora database."""
    name: str  # Database name, e.g., "myapp_prod"
    engine: EngineConfig
    deployment: DeploymentConfig
    backups: BackupConfig
    security: SecurityConfig
    connection: ConnectionConfig


@dataclass
class ContainerConfig:
    """Configuration for a single container in the ECS task definition."""

    name: str  # Stable identifier, used for logs / alb target lookup

    # "dockerfile": DOH builds from app_source_path into ecr_repo_name:image_tag.
    # "prebuilt": image already pushed by an out-of-band step at prebuilt_ecr_repo:prebuilt_version.
    image_source: Literal["dockerfile", "prebuilt"]

    # Populated when image_source == "dockerfile":
    source_repo_path: str | None = None  # Repo-relative path under template_repos/
    dockerfile_path: str | None = None
    ecr_repo_name: str | None = None  # Per-app + per-container ECR repo for the built image

    # Populated when image_source == "prebuilt":
    prebuilt_ecr_repo: str | None = None  # Within doh/{env_slug}/ namespace
    prebuilt_version: str | None = None

    # Network / health
    container_port: int = 0
    health_check_path: str | None = None  # For ALB health check when this container is the alb target
    health_check_command: str | None = None  # For ECS container-level HEALTHCHECK
    health_check_grace_period: int | None = None

    # Runtime config
    environment_variables: list[dict[str, str]] = field(default_factory=list)
    # Secret field names this container consumes. Values are the per-field
    # value semantics used by secrets_utils._resolve_secret_value:
    #   str literal -> use as-is
    #   "" -> fall through to env shared-secrets
    #   None -> auto-generate a random token
    app_secrets: dict[str, str | None] = field(default_factory=dict)

    # Opt-in: mount the task-level EFS volume (AppConfig.efs_config) into this container.
    efs_mount: bool = False

    # Optional override for the container's CMD (the image's ENTRYPOINT is preserved).
    # Useful for prebuilt images whose default CMD doesn't match how DOH wants to
    # run them — e.g. learneo-mcp defaults to stdio but the sidecar integration
    # needs ["--http", "--port", "7777", "--host", "127.0.0.1"].
    command: list[str] | None = None

    # When False, the container's exit won't stop the task. Useful for
    # non-critical sidecars where the ALB-target container can keep serving
    # (degraded) even if the sidecar crashes. Default True (ECS default).
    essential: bool = True


@dataclass
class AppConfig:
    """Configuration for deploying an app to ECS."""

    # Core identifiers
    app_name: str  # e.g., "simple-dashboard" — used in resource names

    # Task-level resources (shared across containers)
    cpu: int  # ECS task CPU units (256, 512, 1024, etc.)
    memory: int  # ECS task memory in MiB

    # Ordered, non-empty list of containers.
    containers: list[ContainerConfig]

    # ECS compute backend for the service.
    compute_mode: ComputeMode = "fargate"

    # Path to app source for the dockerfile-built containers. One path for the
    # whole task today; all dockerfile containers build from subdirectories of
    # this tree (per their source_repo_path/dockerfile_path).
    app_source_path: Path | None = None

    # Name of the container in `containers` that receives ALB traffic.
    # None = no ALB exposure.
    alb_target_container: str | None = None

    # Database configuration (None = no database)
    database_config: DatabaseConfig | None = None

    # Task-level shared-bag app secrets. Computed as the collision-checked
    # union of every container's app_secrets (same field across containers
    # must declare identical values; mismatch raises at build time).
    # Stored in Secrets Manager at devopshero/{env_slug}/{app_name}/secrets
    # and selectively projected into each container's env.
    app_secrets: dict[str, str | None] | None = None

    # EFS volume configuration (None = no EFS volume). Declared once at the
    # task level; per-container mounting is controlled by ContainerConfig.efs_mount.
    efs_config: EfsConfig | None = None

    # Sidecar proxy (SSO + ABAC). When True, the task gets an auth sidecar
    # container wrapping whichever container is named by alb_target_container.
    # See docs/sidecar_proxy_design.md. Orthogonal to the `containers` list.
    sidecar_enabled: bool = False

    # Platform-owned capabilities requested by the source template. CDK maps
    # these to infrastructure grants on the ECS task role.
    platform_capabilities: list[str] = field(default_factory=list)

    def alb_target(self) -> ContainerConfig | None:
        """Return the ALB-target ContainerConfig, or None if no ALB exposure."""
        if not self.alb_target_container:
            return None
        for c in self.containers:
            if c.name == self.alb_target_container:
                return c
        raise ValueError(
            f"alb_target_container='{self.alb_target_container}' not found in containers "
            f"{[c.name for c in self.containers]}"
        )
