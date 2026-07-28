"""
Application configuration dataclass for ECS deployments.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


ComputeMode = Literal["fargate", "ec2"]
ContainerDependencyCondition = Literal["START", "HEALTHY", "COMPLETE", "SUCCESS"]


class ImageSource(StrEnum):
    """How a container's image is sourced at deploy time.

    A single value today; kept explicit as the hook point for future sourcing
    modes (external registries, customer-built images, ...).
    """

    # Image built from the template_path tree under template_repos/ into the
    # shared per-env repo humr/{env_slug}/<image>:{tree-hash}. The deploy path
    # builds the image on miss; every app in the env at the same tree hash
    # shares one image. Fields consumed: template_path.
    TEMPLATE = "template"


class ContainerRole(StrEnum):
    """Platform wiring a container carries beyond how its image is sourced."""

    # SSO + ABAC proxy fronting a sibling container. Receives the env-bearer
    # overlay and the proxy env wiring, and must be the ALB target. Fields
    # consumed: upstream_container (name of the sibling the proxy fronts;
    # resolved into HUMR_UPSTREAM_HOST=127.0.0.1 + HUMR_UPSTREAM_PORT env vars).
    POLICY_PROXY = "policy_proxy"


@dataclass
class ContainerDependencyConfig:
    """ECS container start ordering relative to a sibling container in the same task."""
    name: str
    condition: ContainerDependencyCondition


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
class ContainerConfig:
    """Configuration for a single container in the ECS task definition."""

    name: str  # Stable identifier, used for logs / alb target lookup

    # See the ImageSource enum for what each member means and which fields it
    # consumes on this dataclass.
    image_source: ImageSource

    template_path: str | None = None  # TEMPLATE: tree under template_repos/ this image is built from

    # Platform wiring beyond image sourcing; see the ContainerRole enum.
    role: ContainerRole | None = None

    upstream_container: str | None = None  # role=POLICY_PROXY: sibling container this proxy fronts

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
    command: list[str] | None = None

    # When False, the container's exit won't stop the task. Useful for
    # non-critical sidecars where the ALB-target container can keep serving
    # (degraded) even if the sidecar crashes. Default True (ECS default).
    essential: bool = True

    # Seconds ECS waits after SIGTERM before SIGKILLing the container. None
    # falls through to ECS's default (30s). Bump for containers that do real
    # work in their SIGTERM handler (e.g. humr-dind snapshotting tool state
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
    # bursty-but-usually-idle containers (e.g. two hermes tasks on one t4g.large).
    memory_reservation_mib: int | None = None

    # Container-level CPU reservation in ECS CPU units (1024 = 1 vCPU). On EC2
    # Linux this drives placement and relative CPU shares, not a hard runtime
    # cap — omit task-level cpu when every container sets this so tasks can
    # burst to the full node when neighbors are idle.
    cpu_reservation: int | None = None

    # Opt this container in to the HUMR control-plane bearer overlay:
    # HUMR_ENV_BEARER (from shared-secrets), HUMR_ENV_SLUG, HUMR_APP_SLUG, and
    # HUMR_OWNER_USERNAME (if the owning App has an owner tag). Any env-resident
    # component that calls the HUMR control plane sets this — Hermes integrations
    # today; future env-resident services later. Policy-proxy containers receive
    # the overlay implicitly from role=policy_proxy, so templates do not
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

    # Name of the container in `containers` that receives ALB traffic.
    # None = no ALB exposure.
    alb_target_container: str | None = None

    # Task-level shared-bag app secrets. Computed as the collision-checked
    # union of every container's app_secrets (same field across containers
    # must declare identical values; mismatch raises at build time).
    # Stored in Secrets Manager at humr/{env_slug}/{app_name}/secrets
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
    # one, else None. Injected into env-bearer containers as HUMR_OWNER_USERNAME so
    # they can identify themselves to HUMR's control plane on behalf of this
    # user. None for apps without an owner tag (typical multi-user apps).
    owner_username: str | None = None

    # Slug of the organization that owns the App. Injected into env-bearer
    # containers as HUMR_ORG_SLUG so env-resident components can key
    # org-dependent behavior (e.g. the broker's integration-card visibility).
    org_slug: str | None = None

    # When True, the ALB listener rule also matches *-<agent-host> so Caddy
    # can route user Web Apps by hostname. TLS and DNS come from the
    # environment's *.<zone> certificate and wildcard record. See
    # docs/app_workloads_design.md.
    enable_webapp_hosts: bool = False

    def container_needs_env_bearer(self, container: ContainerConfig) -> bool:
        """True if this container should receive the HUMR control-plane bearer overlay."""
        return container.requires_env_bearer or container.role == ContainerRole.POLICY_PROXY

    def needs_env_bearer(self) -> bool:
        """True if any container in the task needs the HUMR control-plane bearer overlay."""
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
        matches = [c for c in self.containers if c.role == ContainerRole.POLICY_PROXY]
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(
                f"App '{self.app_name}' declares multiple policy_proxy containers: "
                f"{[c.name for c in matches]}. At most one is supported.",
            )
        return matches[0]
