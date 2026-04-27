"""
Application configuration dataclass for ECS deployments.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


ComputeMode = Literal["fargate", "ec2"]
ContainerDependencyCondition = Literal["START", "HEALTHY", "COMPLETE", "SUCCESS"]
ImageSource = Literal["dockerfile", "prebuilt", "registry", "policy_proxy"]


@dataclass
class ContainerDependencyConfig:
    """ECS container start ordering relative to a sibling container in the same task."""
    name: str
    condition: ContainerDependencyCondition


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
class EfsMount:
    """One EFS access point on the task's shared filesystem, mountable by any container."""
    name: str                  # Stable identifier referenced from ContainerConfig.efs_mounts
    subpath: str               # Relative to /deployments/<app>/, e.g. "hermes" or "workspace"
    container_path: str        # Mount target inside a container that opts in
    posix_uid: int
    posix_gid: int


@dataclass
class EfsConfig:
    """EFS configuration for the task: an ordered list of named access points.

    Each mount becomes one EFS AccessPoint + task-level volume. Containers opt
    in by name via ContainerConfig.efs_mounts. Different mounts can have
    different POSIX ownership (e.g. home owned by the app UID, workspace owned
    by root for DinD).
    """
    mounts: list[EfsMount]

    def by_name(self, name: str) -> EfsMount:
        for m in self.mounts:
            if m.name == name:
                return m
        raise ValueError(f"EFS mount '{name}' not declared in efs_config.mounts")


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
    # "registry": public image reference (e.g. docker:26.1.0-dind) resolved by ECS at pull time.
    # "policy_proxy": platform-owned SSO+ABAC proxy; image resolves from the
    #     per-env doh/{env_slug}/policy-proxy repo at deploy time. Presence of
    #     any "policy_proxy" container in a task triggers env-level provisioning
    #     (ECR stack, auth Lambda, per-env auth config secret).
    image_source: ImageSource

    # Populated when image_source == "dockerfile":
    source_repo_path: str | None = None  # Repo-relative path under template_repos/
    dockerfile_path: str | None = None
    ecr_repo_name: str | None = None  # Per-app + per-container ECR repo for the built image

    # Populated when image_source == "prebuilt":
    prebuilt_ecr_repo: str | None = None  # Within doh/{env_slug}/ namespace
    prebuilt_version: str | None = None

    # Populated when image_source == "registry" (e.g. docker:26.1.0-dind, docker.io/...):
    registry_image: str | None = None

    # Populated when image_source == "policy_proxy": name of the sibling
    # container this proxy fronts. Resolved at deploy time into the
    # DOH_UPSTREAM_HOST (always 127.0.0.1) + DOH_UPSTREAM_PORT env vars on the
    # proxy. The upstream container's container_port is the target.
    upstream_container: str | None = None

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

    # Names of EFS mounts (from AppConfig.efs_config.mounts) to bind into this container.
    # Empty = container sees no EFS. Containers can pick any subset independently:
    # Hermes takes ["home", "workspace"], DinD takes ["workspace"] so tool daemons
    # never see agent home/config.
    efs_mounts: list[str] = field(default_factory=list)

    # When True, CDK sets privileged on the container (EC2-only; not supported on Fargate).
    privileged: bool = False

    # Sibling start ordering within the same task.
    depends_on: list[ContainerDependencyConfig] = field(default_factory=list)

    # Optional ECS container user override, e.g. "0" for legacy images.
    user: str | None = None

    # Optional override for the container's CMD (the image's ENTRYPOINT is preserved).
    # Useful for prebuilt images whose default CMD doesn't match how DOH wants to
    # run them — e.g. learneo-mcp defaults to stdio but the sidecar integration
    # needs ["--http", "--port", "7777", "--host", "127.0.0.1"].
    command: list[str] | None = None

    # When False, the container's exit won't stop the task. Useful for
    # non-critical sidecars where the ALB-target container can keep serving
    # (degraded) even if the sidecar crashes. Default True (ECS default).
    essential: bool = True

    # Seconds ECS waits after SIGTERM before SIGKILLing the container. None
    # falls through to ECS's default (30s). Bump for containers that do real
    # work in their SIGTERM handler (e.g. doh-dind snapshotting tool state
    # to EFS, which can run 30-60s for a multi-GB rootfs).
    stop_timeout: int | None = None


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

    # EFS configuration (None = no EFS). A list of named access points declared
    # once at the task level; per-container mounting is controlled by
    # ContainerConfig.efs_mounts referencing entries by name.
    efs_config: EfsConfig | None = None

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

    def policy_proxy_container(self) -> ContainerConfig | None:
        """Return the policy-proxy ContainerConfig if the task includes one, else None."""
        matches = [c for c in self.containers if c.image_source == "policy_proxy"]
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(
                f"App '{self.app_name}' declares multiple policy_proxy containers: "
                f"{[c.name for c in matches]}. At most one is supported.",
            )
        return matches[0]
