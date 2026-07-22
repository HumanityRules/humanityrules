"""
Deploy an application from an AppTemplate.

Creates the Repository + App records and queues the deploy attempt for the
job worker.
"""

import logging

from humanityrules_app import app_slugs
from humanityrules_app import models
from humanityrules_app.services import llm_preset_service
from humanityrules_app.services import sandbox_service
from humanityrules_app.services.jobs import app_job_service

logger = logging.getLogger(__name__)


def _materialize_environment_variables(configurable_variables: list[dict]) -> list[dict[str, str]]:
    """Extract config vars from one container's configurable_variables into {name, value} entries."""
    env_vars: list[dict[str, str]] = []
    for var in configurable_variables:
        if var["category"] != "config":
            continue
        if "value" not in var:
            continue
        value = var["value"]
        if value is None:
            continue
        if value == "" and not var.get("allow_empty_value", False):
            continue
        env_vars.append({"name": var["name"], "value": str(value)})
    return env_vars


def _materialize_app_secrets(configurable_variables: list[dict]) -> dict[str, str | None]:
    """Extract secret vars from one container's configurable_variables into a {name: value} dict."""
    secrets = {}
    for var in configurable_variables:
        if var["category"] != "secret":
            continue
        # None = auto-generate, "" = empty placeholder, "literal" = use as-is
        secrets[var["name"]] = var.get("value")
    return secrets


def _materialize_app_containers(template_containers: list[dict]) -> list[dict]:
    """Project template.containers into App.containers shape.

    Each entry carries `name` (the container identifier), plus the
    materialized `environment_variables` and `app_secrets` derived from that
    container's configurable_variables. Preserves container order.
    """
    result = []
    for c in template_containers:
        cfg_vars = c.get("configurable_variables", [])
        result.append({
            "name": c["name"],
            "environment_variables": _materialize_environment_variables(cfg_vars),
            "app_secrets": _materialize_app_secrets(cfg_vars),
        })
    return result


def _apply_variable_overrides(
    template_containers: list[dict], overrides: dict[str, str] | None,
) -> list[dict]:
    """Return a new containers list with user-supplied values merged into each container's configurable_variables by name."""
    if not overrides:
        return template_containers
    result = []
    for c in template_containers:
        new_cfg_vars = []
        for var in c.get("configurable_variables", []):
            if var["name"] in overrides:
                new_cfg_vars.append({**var, "value": overrides[var["name"]]})
            else:
                new_cfg_vars.append(var)
        result.append({**c, "configurable_variables": new_cfg_vars})
    return result


def _alb_target_container(template: models.AppTemplate) -> dict:
    """Return the container dict named by template.alb_target_container, or containers[0] if unset."""
    containers = template.containers
    if not containers:
        raise ValueError(f"Template '{template.slug}' has no containers")
    if template.alb_target_container:
        for c in containers:
            if c["name"] == template.alb_target_container:
                return c
        raise ValueError(
            f"Template '{template.slug}' alb_target_container='{template.alb_target_container}' "
            f"not found in containers {[c['name'] for c in containers]}"
        )
    return containers[0]


def _primary_build_container(template: models.AppTemplate) -> dict:
    """Return the template container that the App row's identity/build fields describe.

    Normally the ALB target, except when a policy proxy fronts the task: then
    the ALB target is the platform-owned proxy and the real primary is its
    upstream (the dockerfile-built app container).
    """
    target = _alb_target_container(template)
    if target["image_source"] != "policy_proxy":
        return target
    upstream = target.get("upstream_container")
    if not upstream:
        raise ValueError(
            f"Template '{template.slug}' policy-proxy alb_target has no upstream_container",
        )
    for c in template.containers:
        if c["name"] == upstream:
            return c
    raise ValueError(
        f"Template '{template.slug}' upstream_container='{upstream}' not found in containers",
    )


async def _stamp_template_tags(
    organization: models.Organization,
    app: models.App,
    template: models.AppTemplate,
    owner_username: str | None,
) -> None:
    """Write ResourceTag rows for template.default_tags, plus the owner tag for PAs."""
    for tag in (template.default_tags or []):
        await models.ResourceTag.objects.aget_or_create(
            organization=organization,
            resource_type="app",
            app=app,
            key=tag["key"],
            value=tag["value"],
        )
    if owner_username:
        await models.ResourceTag.objects.aget_or_create(
            organization=organization,
            resource_type="app",
            app=app,
            key="owner",
            value=owner_username,
        )


async def _araise_for_hostname_label_conflict(environment: models.Environment, app_slug: str) -> None:
    """Reject a slug whose hostname label an existing app already holds on the environment's hosted zone."""
    hosted_zone = environment.shared_alb_hosted_zone
    if not hosted_zone:
        return
    label_taken = await models.App.objects.filter(environment__shared_alb_hosted_zone=hosted_zone, slug=app_slug).aexists()
    if label_taken:
        raise ValueError(f"'{app_slug}.{hosted_zone}' is already in use. Please choose a different name.")


async def deploy_from_template(
    template: models.AppTemplate,
    organization: models.Organization,
    workspace: models.Workspace,
    environment: models.Environment,
    app_name: str,
    app_slug: str,
    created_by: models.User,
    runtime_variable_overrides: dict[str, str] | None,
    owner_username: str | None,
    compute_mode: str,
    label: str,
) -> models.App:
    """Create Repository + App from a template and queue its first deploy attempt."""
    app_slugs.require_valid_app_hostname_label(value=app_slug)

    # The app serves at its slug. Check the label before any write: a conflict must
    # abort with nothing persisted — no slug claim, no App — so the user can simply
    # retry with a different name.
    await _araise_for_hostname_label_conflict(environment=environment, app_slug=app_slug)

    # Reserve the slug before creating any rows: in the shared sandbox app resources are named
    # humr-sandbox-{slug}-* across all orgs, so the slug is global and first-come. Raises a
    # friendly ValueError if another org holds it, and a race loss here leaves no orphan App.
    await sandbox_service.aclaim_sandbox_app_slug(
        app_slug=app_slug,
        organization_id=organization.id,
        environment=environment,
    )

    # The App row still carries identity/build fields for a single canonical
    # container — the ALB-target one (for multi-container templates) or the
    # sole container (for single-container templates). The rest of the
    # container spec lives on the template and is interpreted at deploy time.
    primary = _primary_build_container(template)
    if primary["image_source"] != "dockerfile":
        raise ValueError(
            f"Template '{template.slug}' primary build container '{primary['name']}' must be "
            f"image_source=dockerfile; got {primary['image_source']}"
        )

    clone_url = f"humr-template://{primary['source_repo_path']}"

    repo, _created = await models.Repository.objects.aget_or_create(
        organization=organization,
        full_name=f"template/{template.slug}",
        defaults={
            "provider": models.Repository.Provider.LOCAL,
            "integration": None,
            "name": template.name,
            "clone_url": clone_url,
            "default_branch": "main",
        },
    )

    # Persist the org's stable LLM preset name; the Hermes container expands it
    # into concrete provider/model settings during startup. Explicit caller
    # overrides (CLI --var) are applied afterward so they still win.
    preset_overrides = llm_preset_service.llm_overrides_for(organization=organization)
    containers_with_preset = _apply_variable_overrides(template.containers, preset_overrides)
    containers_with_overrides = _apply_variable_overrides(containers_with_preset, runtime_variable_overrides)
    app_containers = _materialize_app_containers(containers_with_overrides)

    app = await models.App.objects.acreate(
        organization=organization,
        workspace=workspace,
        environment=environment,
        repository=repo,
        source_template=template,
        name=app_name,
        slug=app_slug,
        build_strategy=models.App.BuildStrategy.DOCKERFILE,
        dockerfile_path=primary.get("dockerfile_path", ""),
        container_port=primary["container_port"],
        health_check_path=primary.get("health_check_path", ""),
        health_check_command=primary.get("health_check_command", ""),
        health_check_grace_period=primary.get("health_check_grace_period", 0),
        cpu=template.cpu,
        memory=template.memory,
        compute_mode=compute_mode,
        containers=app_containers,
        created_by=created_by,
        label=label,
    )

    await _stamp_template_tags(
        organization=organization, app=app, template=template,
        owner_username=owner_username,
    )

    await app_job_service.aqueue_deploy(app=app, created_by=created_by)

    logger.info(
        "Queued template deployment: app=%(app)s, template=%(template)s, environment=%(env)s",
        {"app": app_slug, "template": template.slug, "env": environment.slug},
    )

    return app
