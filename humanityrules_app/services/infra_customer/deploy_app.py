"""
Deploy HumanityRules apps (shared template images + ALB + ECS service) using AWS CDK.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import boto3

from humanityrules_app.models import App as AppModel
from humanityrules_app.models import Environment
from aws_cdk import App, Aws, CfnOutput, Duration, Fn, Stack, Tags
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_efs as efs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from . import appconfig
from . import cdk_utils
from . import cloudformation_utils
from . import deploy_base
from . import secrets_utils
from . import template_images


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


def _resolve_control_plane_url() -> str:
    """Resolve HUMR's control-plane base URL (prod or dev ngrok tunnel).

    In prod env-resident components call humanityrules.io directly. In local dev
    they live in a customer VPC and can't reach the laptop, so we point them
    at a reserved ngrok tunnel that forwards to localhost:8000. If someone
    else ever needs to deploy from their laptop, switch to a per-developer
    setting.
    """
    from django.conf import settings
    return "https://humanityrules.io" if not settings.DEBUG else "https://humanityrules.ngrok.io"


def _resolve_pdp_url() -> str:
    """Resolve the PDP URL the policy proxy should call. HUMR_PDP_URL wins if set."""
    import os
    explicit = os.environ.get("HUMR_PDP_URL")
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
    # Template-image versions this deploy resolved: {image_name: tree_hash}.
    # Stamped onto the DeploymentRecord as the attempt's version history.
    image_hashes: dict[str, str]


# =============================================================================
# CDK STACKS
# =============================================================================


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


def _grants_bedrock_runtime(app_config: appconfig.AppConfig) -> bool:
    """Return True when this deploy carries the bedrock-runtime platform capability.

    The capability comes from the owning org's entitlement, independent of its
    LLM preset: a Codex-preset deploy in a granted org still needs the IAM so a
    user can pick a Bedrock model from the WebUI dropdown (the sandbox's AWS SDK
    config points at the local signing proxy, which signs with the task role).
    """
    return PLATFORM_CAPABILITY_BEDROCK_RUNTIME in app_config.platform_capabilities


def _environment_has_ec2_capacity_provider(session: boto3.Session, env_slug: str) -> bool:
    """Return True when the environment cluster is associated with the EC2 capacity provider."""
    ecs_client = session.client("ecs")
    cluster_name = f"humr-{env_slug}-cluster"
    capacity_provider_name = deploy_base.ec2_capacity_provider_name(env_slug=env_slug)
    response = ecs_client.describe_clusters(clusters=[cluster_name])
    clusters = response.get("clusters", [])
    if not clusters:
        return False
    return capacity_provider_name in clusters[0].get("capacityProviders", [])


def _container_image_uri(container: appconfig.ContainerConfig, account: str, region: str, env_slug: str, image_tags: dict[str, str]) -> str:
    """Resolve the shared template-image URI for a container: humr/{env}/<image>:<tree-hash>."""
    assert container.template_path, f"container '{container.name}' has no template_path"
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com"
    repo_name = template_images.ecr_repo_name(env_slug=env_slug, template_path=container.template_path)
    return f"{registry}/{repo_name}:{image_tags[container.template_path]}"


def _compute_listener_rule_priority(app_name: str) -> int:
    """Compute a deterministic listener rule priority from app name."""
    # Use hash to get a deterministic priority. Range: 1000-41000 (leaving room for manual overrides)
    return (hash(app_name) % 40000) + 1000


class AppStack(Stack):
    """HumanityRules App Stack - ECS Service with shared ALB routing."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        app_config: appconfig.AppConfig,
        image_tags: dict[str, str],
        env_slug: str,
        resource_prefix: str,
        subdomain: str,
        shared_alb_hosted_zone: str | None,
        app_secret_arn: str | None,
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

        # Every container that needs the app bearer requires the app's
        # own secrets-bag ARN (to mount HUMR_APP_BEARER via ECS secret injection).
        # Policy-proxy containers need the same bearer implicitly, plus
        # auth_base_url + upstream wiring. Validate preconditions before
        # building resources.
        if (app_config.needs_app_bearer() or app_config.app_secrets) and not app_secret_arn:
            raise RuntimeError(
                "App needs its per-app secrets bag but app_secret_arn is missing — did the "
                "orchestration skip ensure_app_bearer_token_exists / ensure_app_secrets_exist?",
            )

        # Policy proxy (SSO + ABAC) is opt-in: a template declares a container
        # with role=policy_proxy and points alb_target_container at it.
        policy_proxy = app_config.policy_proxy_container()
        if policy_proxy is not None:
            if not auth_base_url:
                raise RuntimeError(
                    "Policy-proxy deploy requires auth_base_url. Missing — did the "
                    "orchestration skip resolving the control-plane URL?",
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
        # Grant access to this app's own bag and nothing else — it holds both the
        # template-declared secrets (ensure_app_secrets_exist) and the app's
        # app bearer (ensure_app_bearer_token_exists), both written
        # outside CDK. Read on humr/{env}/{app}/* is what keeps one app's
        # credentials unreachable from every other app in the environment.
        if app_config.app_secrets or app_config.needs_app_bearer():
            task_role.add_to_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[f"arn:aws:secretsmanager:{Aws.REGION}:{Aws.ACCOUNT_ID}:secret:humr/{env_slug}/{app_config.app_name}/*"],
            ))
        if _grants_bedrock_runtime(app_config):
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

        # The app's single Secrets Manager bag, imported once and shared by both
        # readers below: the per-container template secrets and the bearer
        # overlay. Each container only sees the fields it declared in its own
        # ContainerConfig.app_secrets, plus HUMR_APP_BEARER when it needs it.
        app_secret_resource: secretsmanager.ISecret | None = None
        if app_secret_arn:
            app_secret_resource = secretsmanager.Secret.from_secret_complete_arn(
                self, "AppSecret", app_secret_arn,
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
            cpu_reservations = [c.cpu_reservation for c in app_config.containers]
            if any(reservation is not None for reservation in cpu_reservations) and not all(
                reservation is not None for reservation in cpu_reservations
            ):
                raise ValueError(
                    f"App '{app_config.app_name}' mixes containers with and without cpu_reservation; "
                    "set cpu_reservation on every container or on none"
                )
            all_have_cpu_reservation = all(reservation is not None for reservation in cpu_reservations)
            # Omit task-level cpu when every container declares cpu_reservation:
            # ECS reserves the sum for placement but Linux CPU shares let a task
            # burst to the full node when neighbors are idle.
            task_cpu = None if all_have_cpu_reservation else str(app_config.cpu)
            task_definition = ecs.TaskDefinition(
                self, "TaskDefinition",
                compatibility=ecs.Compatibility.EC2,
                network_mode=ecs.NetworkMode.AWS_VPC,
                family=resource_prefix[:255],
                cpu=task_cpu,
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
        # 1. Bearer overlay — applied to every container that talks to HUMR's
        #    control plane: explicit requires_app_bearer=True, or a policy-proxy
        #    container.
        #    Provides HUMR_APP_BEARER (from the app's own bag) plus the local
        #    identity vars HUMR_ENV_SLUG, HUMR_CONTROL_PLANE_URL, HUMR_APP_SLUG,
        #    and HUMR_OWNER_USERNAME when the app has an owner tag. The bearer
        #    itself is what the control plane authenticates; the plain vars are
        #    for in-container use (logs, the WebUI status card) only.
        # 2. Policy-proxy-specific overlay — applied only to the policy-proxy
        #    container. Carries JWT verification URL, upstream wiring, etc.
        app_bearer_environment_overlay: dict[str, str] = {}
        app_bearer_secret_overlay: dict[str, ecs.Secret] = {}
        if app_config.needs_app_bearer():
            assert app_secret_resource is not None  # enforced above
            app_bearer_environment_overlay = {
                "HUMR_ENV_SLUG": env_slug,
                "HUMR_CONTROL_PLANE_URL": _resolve_control_plane_url(),
                "HUMR_APP_SLUG": app_config.app_name,
                # Always present (empty when nothing is granted) so in-container
                # gates read an absent capability as denial, not as missing config.
                "HUMR_PLATFORM_CAPABILITIES": ",".join(app_config.platform_capabilities),
            }
            if app_config.owner_username:
                app_bearer_environment_overlay["HUMR_OWNER_USERNAME"] = app_config.owner_username
            if app_config.org_slug:
                app_bearer_environment_overlay["HUMR_ORG_SLUG"] = app_config.org_slug
            if shared_alb_hosted_zone:
                app_bearer_environment_overlay["HUMR_PUBLIC_HOSTNAME"] = f"{subdomain}.{shared_alb_hosted_zone}"
            app_bearer_secret_overlay = {
                secrets_utils.APP_SECRETS_KEY_HUMR_APP_BEARER: ecs.Secret.from_secrets_manager(
                    app_secret_resource, field=secrets_utils.APP_SECRETS_KEY_HUMR_APP_BEARER,
                ),
            }

        policy_proxy_environment_overlay: dict[str, str] = {}
        if policy_proxy is not None:
            assert upstream is not None  # enforced above
            policy_proxy_environment_overlay = {
                "HUMR_APP_ID": app_config.app_name,
                "HUMR_ENV_DOMAIN": shared_alb_hosted_zone or "",
                "HUMR_AUTH_BASE_URL": auth_base_url,
                "HUMR_JWKS_URL": f"{auth_base_url.rstrip('/')}/.well-known/jwks.json",
                "HUMR_PDP_URL": _resolve_pdp_url(),
                "HUMR_UPSTREAM_HOST": "127.0.0.1",
                "HUMR_UPSTREAM_PORT": str(upstream.container_port),
                "HUMR_LISTEN_PORT": str(policy_proxy.container_port),
            }

        # Add each configured container to the task definition.
        containers_by_name: dict[str, ecs.ContainerDefinition] = {}
        for idx, c in enumerate(app_config.containers):
            image_uri = _container_image_uri(
                container=c,
                account=self.account,
                region=self.region,
                env_slug=env_slug,
                image_tags=image_tags,
            )

            container_needs_app_bearer = app_config.container_needs_app_bearer(container=c)

            # Environment: per-container list of {name, value}, plus the
            # app bearer overlay for every container that needs it, plus the
            # policy-proxy-specific overlay on the policy-proxy container.
            environment = {e["name"]: e["value"] for e in c.environment_variables}
            if container_needs_app_bearer:
                environment.update(app_bearer_environment_overlay)
            if c.role == appconfig.ContainerRole.POLICY_PROXY:
                environment.update(policy_proxy_environment_overlay)

            # Secrets: the container's declared fields from the shared app_secrets bag,
            # plus HUMR_APP_BEARER on any container that needs it.
            secrets: dict[str, ecs.Secret] = {}
            if app_secret_resource is not None and c.app_secrets:
                for field_name in c.app_secrets:
                    secrets[field_name] = ecs.Secret.from_secrets_manager(app_secret_resource, field=field_name)
            if container_needs_app_bearer:
                secrets.update(app_bearer_secret_overlay)

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
                cpu=c.cpu_reservation,
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

        # When HUMR runs in production (DEBUG=False), use stable settings
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
            target_group_name=f"humr-{env_slug}-{app_config.app_name}"[:32].rstrip("-"),
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

        # Configure routing rules on the shared ALB.
        self._setup_shared_alb_routing(
            app_config=app_config,
            subdomain=subdomain,
            env_slug=env_slug,
            resource_prefix=resource_prefix,
            target_group=target_group,
            shared_alb_hosted_zone=shared_alb_hosted_zone,
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
        # Binpack instead of the default AZ spread: fill the fullest node
        # first so idle nodes empty out and managed scaling can terminate
        # them. Spread kept re-provisioning a node per AZ, doubling instance
        # cost for single-task services. With AZ rebalancing ENABLED, CDK
        # requires spread-by-AZ to come before binpack, so that branch packs
        # only within the spread-chosen AZ.
        if app_config.serialize_task_replacement:
            max_healthy = 100
            az_rebalancing = ecs.AvailabilityZoneRebalancing.DISABLED
            placement_strategies = [ecs.PlacementStrategy.packed_by(ecs.BinPackResource.MEMORY)]
        else:
            max_healthy = 200
            az_rebalancing = ecs.AvailabilityZoneRebalancing.ENABLED
            placement_strategies = [
                ecs.PlacementStrategy.spread_across(ecs.BuiltInAttributes.AVAILABILITY_ZONE),
                ecs.PlacementStrategy.packed_by(ecs.BinPackResource.MEMORY),
            ]
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
                placement_strategies=placement_strategies,
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
    ) -> None:
        """Configure the app's routing rules on the shared ALB.

        The rule matches the agent hostname and, when webapp hosts are
        enabled, *-<agent-host>. TLS comes from the environment listener's
        default *.<zone> certificate. The base stack's *.<zone> DNS record
        sends both hostname shapes to the shared ALB.
        """
        priority = _compute_listener_rule_priority(subdomain)
        prefix = f"humr-{env_slug}"

        shared_alb_dns = Fn.import_value(f"{prefix}-shared-alb-dns")

        # Both hostname shapes share one ALB rule and target group. Caddy
        # distinguishes the agent root from webapp hosts by forwarded host.
        webapp_hosts_enabled = bool(app_config.enable_webapp_hosts and shared_alb_hosted_zone)

        if shared_alb_hosted_zone:
            # HTTPS mode: one host rule on :443. The :80 listener's default
            # redirects every host to HTTPS, so no per-host :80 rule is needed.
            app_hostname = f"{subdomain}.{shared_alb_hosted_zone}"
            host_patterns = [app_hostname, f"*-{app_hostname}"] if webapp_hosts_enabled else [app_hostname]

            https_listener = elbv2.ApplicationListener.from_application_listener_attributes(
                self, "ImportedHttpsListener",
                listener_arn=self.environment_infra.shared_alb_https_listener_arn,
                security_group=self.environment_infra.shared_alb_security_group,
            )

            elbv2.ApplicationListenerRule(
                self, "HttpsListenerRule",
                listener=https_listener,
                priority=priority,
                conditions=[elbv2.ListenerCondition.host_headers(host_patterns)],
                target_groups=[target_group],
            )

            CfnOutput(self, "HttpsUrl", value=f"https://{app_hostname}", export_name=f"{resource_prefix}-https-url")
        else:
            # HTTP-only mode: no HTTPS listener, so route by path prefix on :80.
            http_listener = elbv2.ApplicationListener.from_application_listener_attributes(
                self, "ImportedHttpListener",
                listener_arn=self.environment_infra.shared_alb_http_listener_arn,
                security_group=self.environment_infra.shared_alb_security_group,
            )

            elbv2.ApplicationListenerRule(
                self, "HttpListenerRule",
                listener=http_listener,
                priority=priority,
                conditions=[elbv2.ListenerCondition.path_patterns([f"/{subdomain}/*"])],
                target_groups=[target_group],
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
    build_id: str,
    env_slug: str,
    environment: Environment,
    app: AppModel,
    subdomain: str,
    synth_only: bool,
    shared_alb_hosted_zone: str | None,
) -> DeployResult:
    """
    Deploy an app to existing infrastructure.

    Assumes VPC and ECS cluster are already deployed (run deploy_base first).
    Container images are shared per-env template images resolved by tree hash;
    a missing image is built on the spot (see template_images).

    Args:
        session: Boto3 session with assumed role credentials.
        account_id: Target AWS account ID.
        region: Target AWS region.
        app_config: Application configuration.
        build_id: Unique id for this attempt, isolating any image builds on the shared EC2 builder.
        env_slug: Environment slug (e.g., "default", "prod").
        environment: The Environment row the app is deployed into.
        app: The App row being deployed — the per-app bearer token hangs off it.
        subdomain: Hostname label this deployment is served under.
        synth_only: If True, only synthesize templates, don't deploy.
        shared_alb_hosted_zone: Hosted zone for shared ALB (e.g., "dev.example.com"). None = HTTP only.
    Returns:
        DeployResult with success flag and extracted URLs.
    """
    logger.info("Deploying app '%(app_name)s' to environment '%(env_slug)s'", {"app_name": app_config.app_name, "env_slug": env_slug})

    # Resource prefix for consistent naming: humr-{env}-{app} (app slugs are per organization unique)
    resource_prefix = f"humr-{env_slug}-{app_config.app_name}"

    cf_client = session.client("cloudformation")

    cloudformation_utils.cleanup_rollback_complete_stacks(cf_client, [f"{resource_prefix}-app"])

    # Verify infrastructure exists
    vpc_stack_name = f"humr-{env_slug}-vpc"
    cluster_stack_name = f"humr-{env_slug}-cluster"

    if not cloudformation_utils.stack_exists(cf_client, vpc_stack_name):
        msg = f"Base layer not deployed. VPC stack '{vpc_stack_name}' not found"
        logger.error(msg)
        return DeployResult(success=False, error=msg, service_url="", alb_dns="", image_hashes={})
    if not cloudformation_utils.stack_exists(cf_client, cluster_stack_name):
        msg = f"ECS cluster not deployed. Cluster stack '{cluster_stack_name}' not found"
        logger.error(msg)
        return DeployResult(success=False, error=msg, service_url="", alb_dns="", image_hashes={})
    if app_config.compute_mode == "ec2" and not _environment_has_ec2_capacity_provider(session=session, env_slug=env_slug):
        capacity_provider_name = deploy_base.ec2_capacity_provider_name(env_slug=env_slug)
        msg = (
            f"EC2 compute requested, but capacity provider '{capacity_provider_name}' is not associated with "
            f"cluster 'humr-{env_slug}-cluster'. Redeploy the environment base infrastructure first."
        )
        logger.error(msg)
        return DeployResult(success=False, error=msg, service_url="", alb_dns="", image_hashes={})

    # Resolve shared + app-level secrets in Secrets Manager (created outside CDK for security)
    if app_config.app_secrets:
        logger.info("Ensuring app secrets exist")
        shared_secrets = secrets_utils.get_shared_secrets(session=session, env=environment)
        secrets_utils.ensure_app_secrets_exist(session=session, env_slug=env_slug, app_config=app_config, shared_secrets=shared_secrets)

    # App bearer prerequisite: HUMR_APP_BEARER inside the app's own
    # secrets bag + the matching AppBearerToken row. Runs after the template
    # secrets above so its read-modify-write of one key preserves them, and it
    # creates the bag when no template declared any secret at all.
    policy_proxy_needed = app_config.policy_proxy_container() is not None
    app_bearer_needed = app_config.needs_app_bearer()
    app_secret_arn: str | None = None

    if app_bearer_needed:
        logger.info("Ensuring per-app bearer token exists")
        app_secret_arn = secrets_utils.ensure_app_bearer_token_exists(
            session=session, env_slug=env_slug, app=app,
        )
    elif app_config.app_secrets:
        app_secret_arn = secrets_utils.app_secrets_secret_arn(session=session, env_slug=env_slug, app_slug=app_config.app_name)

    # Policy-proxy prerequisites: HTTPS (the session cookie is host-scoped) and
    # the control-plane URL the sidecar bounces unauthenticated requests to.
    policy_proxy_auth_base_url: str | None = None
    if policy_proxy_needed:
        if not shared_alb_hosted_zone:
            msg = "Policy-proxy apps require a hosted zone (HTTPS)"
            logger.error(msg)
            return DeployResult(success=False, error=msg, service_url="", alb_dns="", image_hashes={})

        policy_proxy_auth_base_url = _resolve_control_plane_url()

    # Phase 1: Resolve every container's shared template image, building on
    # miss. In synth-only mode just compute the tags — no AWS calls.
    template_paths = sorted({c.template_path for c in app_config.containers if c.template_path})
    image_tags: dict[str, str] = {}
    if synth_only:
        from django.conf import settings
        image_tags = {tp: template_images.tree_hash(settings.TEMPLATE_REPOS_DIR / tp) for tp in template_paths}
    else:
        for template_path in template_paths:
            try:
                image_tags[template_path] = template_images.ensure_template_image(
                    session=session,
                    account_id=account_id,
                    region=region,
                    env_slug=env_slug,
                    template_path=template_path,
                    build_id=build_id,
                )
            except template_images.TemplateImageBuildError as exc:
                logger.error("Template image resolution failed: %(error)s", {"error": str(exc)})
                return DeployResult(success=False, error=str(exc), service_url="", alb_dns="", image_hashes={})

    image_hashes = {template_images.image_name(tp): tag for tp, tag in image_tags.items()}

    # Phase 2: Deploy the App stack (images exist, so ECS can start tasks immediately).
    with cdk_utils.jsii_synth_lock:
        cdk_app = App(outdir=str(cdk_utils.create_synth_dir(name=resource_prefix)))
        AppStack(
            scope=cdk_app,
            construct_id=f"{resource_prefix}-app",
            app_config=app_config,
            image_tags=image_tags,
            env_slug=env_slug,
            resource_prefix=resource_prefix,
            subdomain=subdomain,
            shared_alb_hosted_zone=shared_alb_hosted_zone,
            app_secret_arn=app_secret_arn,
            auth_base_url=policy_proxy_auth_base_url,
        )
        assembly_dir = cdk_utils.synth_cdk_app(cdk_app)

    if synth_only:
        return DeployResult(success=True, error="", service_url="", alb_dns="", image_hashes=image_hashes)

    if not cdk_utils.deploy_from_assembly(assembly_dir=assembly_dir, session=session, stack_names=[f"{resource_prefix}-app"]):
        logger.error("CDK deployment failed (App)")
        return DeployResult(success=False, error="CDK deployment failed (App)", service_url="", alb_dns="", image_hashes=image_hashes)

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
        image_tag=",".join(f"{name}:{tag}" for name, tag in sorted(image_hashes.items())),
        service_url=service_url,
        alb_dns=alb_dns,
    )

    return DeployResult(success=True, error="", service_url=service_url, alb_dns=alb_dns, image_hashes=image_hashes)


def teardown(session: boto3.Session, env_slug: str, app_name: str) -> bool:
    """Delete the app's CDK stack (ALB routing + ECS service).

    Template-image repos are per-env shared resources owned by their own
    stacks; per-app teardown never touches them.
    """
    cf_client = session.client("cloudformation")

    stack_name = f"humr-{env_slug}-{app_name}-app"

    logger.info("Tearing down app: %(app_name)s (stack %(stack_name)s)", {"app_name": app_name, "stack_name": stack_name})

    success = cloudformation_utils.delete_stack_and_wait(cf_client, stack_name=stack_name)
    if success:
        logger.info("App '%(app_name)s' stack deleted successfully", {"app_name": app_name})
    else:
        logger.error("App '%(app_name)s' stack failed to delete", {"app_name": app_name})
    return success
