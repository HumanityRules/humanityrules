"""
Build AppConfig from Django models.

Converts a Django App model (with related Workspace, Environment)
into an appconfig.AppConfig suitable for CDK deployment.
"""

from humanityrules_app.models import App, AppTemplate, Organization, ResourceTag
from humanityrules_app.services import infra_customer
from humanityrules_app.services import llm_preset_service
from humanityrules_app.services import template_deploy_service
from humanityrules_app.services.billing import plans
from humanityrules_app.services.infra_customer.appconfig import (
    AppConfig,
    ContainerConfig,
    ContainerDependencyConfig,
    ContainerRole,
    ImageSource,
)


# Keys the control plane writes into humr/{env}/{app}/secrets itself; a template
# may not declare them (see _union_app_secrets).
RESERVED_APP_SECRET_NAMES = frozenset({infra_customer.secrets_utils.APP_SECRETS_KEY_HUMR_APP_BEARER})


def _merge_container_environment(
    template_container: dict, app_container: dict,
) -> list[dict[str, str]]:
    """Merge sources of env vars for one container; later sources override earlier ones.

    Precedence (low → high):
      1. template.environment    — platform-constant env vars, a plain {name: value} dict.
         HUMR-managed, never shown in the deploy form.
      2. template.configurable_variables — per-deployment knobs materialized into
         {name, value} entries at deploy time (user values, defaults, or auto-generated).
      3. app.environment_variables — the snapshot of (2) taken when the app was
         created, which may already reflect operator overrides.
    """
    merged: dict[str, str] = {}
    for name, value in (template_container.get("environment") or {}).items():
        merged[name] = str(value)
    cfg_list = template_deploy_service._materialize_environment_variables(
        template_container.get("configurable_variables", []),
    )
    for e in cfg_list:
        merged[e["name"]] = e["value"]
    for e in app_container.get("environment_variables") or []:
        merged[e["name"]] = e["value"]
    return [{"name": name, "value": value} for name, value in merged.items()]


class ContainerSecretCollision(ValueError):
    """Two containers declared the same secret field name with different values."""


class PlatformCapabilityNotGranted(ValueError):
    """The app's configuration needs a platform capability its org's plan does not enable."""


class ReservedSecretName(ValueError):
    """A container declared a secret field name the control plane owns."""


def effective_platform_capabilities(organization: Organization) -> list[str]:
    """Return the platform-capability slugs this organization's deploys receive.

    A platform capability is an infra feature HumR grants a deployed agent —
    for example Bedrock runtime access. Which ones an organization gets is
    decided by its effective plan (plan tier plus any ``plan_overrides``), then
    mapped to a fixed list of slugs by ``PlanConfig.platform_capability_slugs``.

    That list has to be computed in exactly one place. Deploy wiring feeds the
    same result into both the ECS task-role IAM grants and the container env
    var ``HUMR_PLATFORM_CAPABILITIES``. If those two paths ever diverged, the
    agent could believe a capability was available while AWS denied the call,
    or the reverse.

    Also refuses a deploy whose LLM preset is Bedrock when the plan does not
    enable ``bedrock-runtime``: the default model would have no IAM behind it.
    """
    capabilities = plans.effective_plan(organization=organization).platform_capability_slugs()
    preset = llm_preset_service.resolve_preset_name(organization=organization)
    bedrock_runtime = infra_customer.deploy_app.PLATFORM_CAPABILITY_BEDROCK_RUNTIME
    if preset == Organization.LlmPreset.BEDROCK.value and bedrock_runtime not in capabilities:
        raise PlatformCapabilityNotGranted(
            f"Organization '{organization.slug}' defaults its assistants to Bedrock but its plan "
            f"does not enable the '{bedrock_runtime}' platform capability; the deployed agent would "
            "have no IAM behind its default model. Set plan_overrides = {\"bedrock_enabled\": true} "
            "on the organization, move it to a plan that enables Bedrock, or change the LLM preset."
        )
    return capabilities


def _union_app_secrets(containers: list[ContainerConfig]) -> dict[str, str | None]:
    """Collision-checked union of each container's app_secrets.

    Same field name across containers must declare identical values (literal,
    ""-placeholder, or None-auto-generate all compared by equality). Names the
    control plane owns inside the per-app bag are reserved: a template that
    declared HUMR_APP_BEARER would have its value overwritten by the mint on
    every deploy, so we refuse it instead of letting it silently lose.
    """
    merged: dict[str, str | None] = {}
    owners: dict[str, str] = {}
    for c in containers:
        for field_name, value in c.app_secrets.items():
            if field_name in RESERVED_APP_SECRET_NAMES:
                raise ReservedSecretName(
                    f"Container '{c.name}' declares secret '{field_name}', which is "
                    f"reserved for the control plane's per-app bearer token"
                )
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
    app_container: dict,
    app_name: str,
    env_slug: str,
) -> ContainerConfig:
    """Project a (template, app) container pair into a ContainerConfig."""
    name = template_container["name"]
    # ImageSource(...) hard-fails on pre-template-image spec shapes
    # ("dockerfile", "prebuilt", ...) — re-run seed_app_templates.
    image_source = ImageSource(template_container["image_source"])
    role = ContainerRole(template_container["role"]) if template_container.get("role") else None

    if not template_container.get("template_path"):
        raise ValueError(f"Container '{name}' has no template_path")
    if role == ContainerRole.POLICY_PROXY and not template_container.get("upstream_container"):
        raise ValueError(f"Container '{name}' is role=policy_proxy but has no upstream_container")

    command = template_container.get("command")
    return ContainerConfig(
        name=name,
        image_source=image_source,
        template_path=template_container["template_path"],
        role=role,
        upstream_container=template_container.get("upstream_container") or None,
        container_port=template_container["container_port"],
        health_check_path=template_container.get("health_check_path") or None,
        health_check_command=template_container.get("health_check_command") or None,
        health_check_grace_period=template_container.get("health_check_grace_period") or None,
        environment_variables=_merge_container_environment(
            template_container=template_container, app_container=app_container,
        ),
        app_secrets=dict(app_container.get("app_secrets") or {}),
        efs_mounts=list(template_container.get("efs_mounts") or []),
        host_mounts=[
            infra_customer.appconfig.HostMount(
                source_path=str(m["source_path"]).format(app_slug=app_name, env_slug=env_slug),
                container_path=str(m["container_path"]),
            )
            for m in template_container.get("host_mounts", [])
        ],
        privileged=bool(template_container.get("privileged", False)),
        linux_capabilities=list(template_container.get("linux_capabilities") or []),
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
        stop_timeout=template_container.get("stop_timeout") or None,
        memory_limit_mib=template_container.get("memory_limit_mib") or None,
        memory_reservation_mib=template_container.get("memory_reservation_mib") or None,
        cpu_reservation=template_container.get("cpu_reservation") or None,
        requires_app_bearer=bool(template_container.get("requires_app_bearer", False)),
    )


def _match_app_containers_to_template(
    template_containers: list[dict],
    app_containers: list[dict],
) -> dict[str, dict]:
    """Return a {name: app_container_dict} lookup, falling back to empty for missing names."""
    by_name = {c["name"]: c for c in app_containers}
    return {c["name"]: by_name.get(c["name"], {"name": c["name"]}) for c in template_containers}


def build_app_config_from_app(app: App) -> AppConfig:
    """Build an AppConfig sourcing identity from App + template and runtime from the App's materialized fields."""
    environment = app.environment
    template: AppTemplate = app.source_template

    if not template.containers:
        raise ValueError(f"App '{app.slug}' template '{template.slug}' has no containers")

    efs_config = None
    if template.efs_config:
        raw = template.efs_config
        efs_config = infra_customer.appconfig.EfsConfig(
            mounts=[
                infra_customer.appconfig.EfsMount(
                    name=m["name"],
                    subpath=m["subpath"],
                    container_path=m["container_path"],
                    posix_uid=m["posix_uid"],
                    posix_gid=m["posix_gid"],
                )
                for m in raw["mounts"]
            ],
        )

    # Resolve any fills_node_slot marker into concrete reservations for this
    # environment's node profile; downstream only ever sees numbers.
    template_containers = infra_customer.node_packing.resolve_slot_fillers(
        containers=template.containers, eni_trunking_enabled=environment.eni_trunking_enabled,
    )

    app_container_by_name = _match_app_containers_to_template(
        template_containers=template_containers,
        app_containers=app.containers or [],
    )

    containers = [
        _build_container_config(
            template_container=tc,
            app_container=app_container_by_name[tc["name"]],
            app_name=app.slug,
            env_slug=environment.slug,
        )
        for tc in template_containers
    ]

    app_secrets_union = _union_app_secrets(containers)

    owner_username = _owner_username_for_app(app=app)

    return AppConfig(
        app_name=app.slug,
        cpu=app.cpu,
        memory=app.memory,
        containers=containers,
        compute_mode=app.compute_mode,
        alb_target_container=template.alb_target_container,
        app_secrets=app_secrets_union or None,
        efs_config=efs_config,
        platform_capabilities=effective_platform_capabilities(organization=app.organization),
        owner_username=owner_username,
        org_slug=app.organization.slug,
        serialize_task_replacement=template.serialize_task_replacement,
        enable_webapp_hosts=template.enable_webapp_hosts,
    )


def _owner_username_for_app(app: App) -> str | None:
    """Return the `owner` ResourceTag value for *app*, or None if the app has no owner tag.

    The owner tag is set by the Personal Assistant deploy flow (see
    docs/policy_proxy_design.md). It's the identity HUMR injects into app-bearer
    containers as HUMR_OWNER_USERNAME so they can speak to the HUMR control plane on
    behalf of this user.
    """
    row = ResourceTag.objects.filter(
        resource_type="app", app=app, key="owner",
    ).first()
    return row.value if row is not None else None
