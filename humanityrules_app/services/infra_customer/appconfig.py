"""
Application configuration dataclass for ECS deployments.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal


ComputeMode = Literal["fargate", "ec2"]
ContainerDependencyCondition = Literal["START", "HEALTHY", "COMPLETE", "SUCCESS"]


class ImageSource(StrEnum):
    """How a container's image is sourced at deploy time.

    This is the single source of truth for what each value means; the rest of
    the codebase dispatches on these members.
    """

    # DOH builds the image from source during the deploy. The source tree lives
    # under template_repos/ (or the customer's cloned repo) and is pushed to
    # the per-app ECR repo `{c.ecr_repo_name}:{image_tag}`. Fields consumed:
    # source_repo_path, dockerfile_path, ecr_repo_name.
    DOCKERFILE = "dockerfile"

    # Image already exists in the per-env ECR namespace (doh/{env_slug}/{repo})
    # and was pushed out-of-band by `doh_build_prebuilt_image`. The deploy does
    # not build — a missing image is a hard-fail. Fields consumed:
    # prebuilt_ecr_repo, prebuilt_version.
    PREBUILT = "prebuilt"

    # Public registry reference resolved by ECS at pull time (e.g.
    # "docker:26.1.0-dind", "docker.io/..."). Fields consumed: registry_image.
    REGISTRY = "registry"

    # Platform-owned SSO + ABAC proxy. Image resolves from the per-env
    # doh/{env_slug}/policy-proxy repo (pushed by deploy_app at
    # POLICY_PROXY_IMAGE_VERSION). Presence of any policy_proxy container in a
    # task triggers env-level provisioning (ECR stack, auth Lambda, per-env
    # auth config secret) and forces the proxy to be the ALB target. Fields
    # consumed: upstream_container (name of the sibling the proxy fronts;
    # resolved into DOH_UPSTREAM_HOST=127.0.0.1 + DOH_UPSTREAM_PORT env vars).
    POLICY_PROXY = "policy_proxy"


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
class HostMount:
    """One EC2 host-path bind mount for an EC2-backed ECS container."""
    source_path: str
    container_path: str


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

    # See the ImageSource enum for what each member means and which fields it
    # consumes on this dataclass.
    image_source: ImageSource

    source_repo_path: str | None = None  # DOCKERFILE: repo-relative path under template_repos/
    dockerfile_path: str | None = None   # DOCKERFILE
    ecr_repo_name: str | None = None     # DOCKERFILE: per-app + per-container ECR repo

    prebuilt_ecr_repo: str | None = None  # PREBUILT: within doh/{env_slug}/ namespace
    prebuilt_version: str | None = None   # PREBUILT

    registry_image: str | None = None  # REGISTRY

    upstream_container: str | None = None  # POLICY_PROXY: sibling container this proxy fronts

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

    # EC2 host-path bind mounts for this container. These are intentionally
    # separate from EFS mounts: the source path lives on the ECS container
    # instance, not in an AWS-managed network filesystem.
    host_mounts: list[HostMount] = field(default_factory=list)

    # When True, CDK sets privileged on the container (EC2-only; not supported on Fargate).
    privileged: bool = False

    # Linux capabilities added to the container. Keep this narrow; Hermes uses
    # SYS_ADMIN only to mount proc/dev/sys into its persistent chroot before
    # entering the sandboxed runtime.
    linux_capabilities: list[str] = field(default_factory=list)

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

    # Container-level hard memory ceiling in MiB. Over = OOM-kill. None = no
    # per-container hard cap (container can use all task-level memory). On
    # EC2, either this or the task-level memory must be set somewhere in the
    # task; on Fargate the task-level memory is always the ceiling.
    memory_limit_mib: int | None = None

    # Container-level soft memory reservation in MiB. ECS uses this for
    # placement reservation on EC2 tasks when a container has no hard cap
    # (memory_limit_mib=None); when both are set, memory_limit_mib is the
    # hard cap and memory_reservation_mib is a cgroup soft limit Docker
    # squeezes toward under memory pressure. Lets two tasks share a node with
    # bursty-but-usually-idle containers (e.g. two hermes tasks on one m8g).
    memory_reservation_mib: int | None = None

    # Container-level CPU reservation in ECS CPU units (1024 = 1 vCPU). On EC2
    # Linux this drives placement and relative CPU shares, not a hard runtime
    # cap — omit task-level cpu when every container sets this so tasks can
    # burst to the full node when neighbors are idle.
    cpu_reservation: int | None = None

    # Opt this container in to the DOH control-plane bearer overlay:
    # DOH_ENV_BEARER (from shared-secrets), DOH_ENV_SLUG, DOH_APP_SLUG, and
    # DOH_OWNER_USERNAME (if the owning App has an owner tag). Any env-resident
    # component that calls the DOH control plane sets this — Hermes integrations
    # today; future env-resident services later. Policy-proxy containers receive
    # the overlay implicitly from image_source=policy_proxy, so templates do not
    # need to set this knob for them. The IAM grant to read shared-secrets is
    # added to the task role iff any container needs the overlay.
    requires_env_bearer: bool = False


@dataclass
class AppConfig:
    """Configuration for deploying an app to ECS."""

    # Core identifiers
    app_name: str  # e.g., "simple-dashboard" — used in resource names

    # Task-level resources (shared across containers)
    cpu: int  # ECS task CPU units on Fargate; on EC2 used only when containers omit cpu_reservation
    # Task-level memory in MiB. Always applied on Fargate (where it's the
    # task size). On EC2 it's also applied as the task-level ceiling unless
    # every container sets its own memory_limit_mib, in which case the
    # task-level cap is omitted and placement reserves the sum of container
    # reservations — this is how we get two 2-GiB-reserved hermes tasks on
    # one 8-GiB node.
    memory: int

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

    # When True, the ECS service is configured with max_healthy_percent=100 so
    # the old task stops completely before the replacement starts. Required for
    # checkpoint-based persistence (Hermes tars /hermes-persistent-root into
    # EFS on SIGTERM; the replacement task must wait for that write to land or
    # it boots from the image and drops conversation state).
    serialize_task_replacement: bool = False

    # Owner's username (from the App's `owner` ResourceTag) when the app has
    # one, else None. Injected into env-bearer containers as DOH_OWNER_USERNAME so
    # they can identify themselves to DOH's control plane on behalf of this
    # user. None for apps without an owner tag (typical multi-user apps).
    owner_username: str | None = None

    # When True, deploy provisions per-agent wildcard infra so user webapps
    # can be reached at <slug>.<agent-host> instead of <agent-host>/webapps/<slug>/.
    # CDK issues an ACM cert for *.<agent-host>, a wildcard A-alias DNS
    # record, and widens the ALB listener-rule host condition to include
    # *.<agent-host>. The in-container Caddy sidecar then routes by Host
    # header. See docs/webapps_design.md.
    enable_subhosting: bool = False

    def container_needs_env_bearer(self, container: ContainerConfig) -> bool:
        """True if this container should receive the DOH control-plane bearer overlay."""
        return container.requires_env_bearer or container.image_source == ImageSource.POLICY_PROXY

    def needs_env_bearer(self) -> bool:
        """True if any container in the task needs the DOH control-plane bearer overlay."""
        return any(self.container_needs_env_bearer(container=c) for c in self.containers)

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
        matches = [c for c in self.containers if c.image_source == ImageSource.POLICY_PROXY]
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(
                f"App '{self.app_name}' declares multiple policy_proxy containers: "
                f"{[c.name for c in matches]}. At most one is supported.",
            )
        return matches[0]
