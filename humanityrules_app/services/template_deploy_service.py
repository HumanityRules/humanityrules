"""
Deploy an application from an AppTemplate.

Creates the App record and queues the deploy attempt for the job worker.
"""

import logging

from django.db import transaction

from humanityrules_app import app_slugs
from humanityrules_app import models
from humanityrules_app.services import llm_preset_service
from humanityrules_app.services import sandbox_service
from humanityrules_app.services.billing import plans
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
    """Return the template container that the App row's identity fields describe.

    Normally the ALB target, except when a policy proxy fronts the task: then
    the ALB target is the platform-owned proxy and the real primary is its
    upstream (the app container).
    """
    target = _alb_target_container(template)
    if target.get("role") != "policy_proxy":
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


def _stamp_template_tags(
    organization: models.Organization,
    app: models.App,
    template: models.AppTemplate,
    owner_username: str | None,
) -> None:
    """Write ResourceTag rows for template.default_tags, plus the owner tag for PAs."""
    for tag in (template.default_tags or []):
        models.ResourceTag.objects.get_or_create(
            organization=organization,
            resource_type="app",
            app=app,
            key=tag["key"],
            value=tag["value"],
        )
    if owner_username:
        models.ResourceTag.objects.get_or_create(
            organization=organization,
            resource_type="app",
            app=app,
            key="owner",
            value=owner_username,
        )


def is_agent_template(template: models.AppTemplate) -> bool:
    """True iff the template deploys a personal agent: policy-proxy-fronted and tagged as one.

    The same predicate answers two questions — an agent needs an owner at deploy
    time, and an agent is what the plan's max_agents counts.
    """
    has_policy_proxy = any(
        container.get("role") == "policy_proxy"
        for container in (template.containers or [])
    )
    if not has_policy_proxy:
        return False
    return any(
        tag.get("key") == "app-type" and tag.get("value") == "personal-assistant"
        for tag in (template.default_tags or [])
    )


def _live_agent_app_count(organization: models.Organization) -> int:
    """Agent apps the organization still holds; a removal in flight no longer counts against it."""
    agent_template_ids = [
        template.id for template in models.AppTemplate.objects.all()
        if is_agent_template(template=template)
    ]
    return (
        models.App.objects
        .filter(organization=organization, source_template_id__in=agent_template_ids)
        .exclude(job_status__in=models.App.REMOVAL_JOB_STATUSES)
        .count()
    )


def _raise_for_agent_limit(organization: models.Organization, template: models.AppTemplate) -> None:
    """Reject an agent deploy that would take the organization past its plan's max_agents."""
    if not is_agent_template(template=template):
        return
    max_agents = plans.effective_plan(organization=organization).max_agents
    live_agents = _live_agent_app_count(organization=organization)
    if live_agents >= max_agents:
        raise ValueError(
            f"Your plan includes {max_agents} agent(s) and you already have {live_agents}. "
            "Remove an existing agent or upgrade your plan to deploy another."
        )


def _raise_for_hostname_label_conflict(environment: models.Environment, app_slug: str) -> None:
    """Reject a slug whose hostname label an existing app already holds on the environment's hosted zone."""
    hosted_zone = environment.shared_alb_hosted_zone
    if not hosted_zone:
        return
    label_taken = models.App.objects.filter(environment__shared_alb_hosted_zone=hosted_zone, slug=app_slug).exists()
    if label_taken:
        raise ValueError(f"'{app_slug}.{hosted_zone}' is already in use. Please choose a different name.")


def deploy_from_template(
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
    """Create an App from a template and queue its first deploy attempt."""
    app_slugs.require_valid_app_hostname_label(value=app_slug)

    # The App row still carries identity fields (port, health checks) for a
    # single canonical container — the ALB-target one, or the policy proxy's
    # upstream. The rest of the container spec lives on the template and is
    # interpreted at deploy time.
    primary = _primary_build_container(template)

    # Persist the org's stable LLM preset name; the Hermes container expands it
    # into concrete provider/model settings during startup. Explicit caller
    # overrides (CLI --var) are applied afterward so they still win.
    preset_overrides = llm_preset_service.llm_overrides_for(organization=organization)
    containers_with_preset = _apply_variable_overrides(template.containers, preset_overrides)
    containers_with_overrides = _apply_variable_overrides(containers_with_preset, runtime_variable_overrides)
    app_containers = _materialize_app_containers(containers_with_overrides)

    with transaction.atomic():
        locked_environment = (
            models.Environment.objects
            .select_for_update(of=("self",))
            .select_related("aws_account")
            .get(id=environment.id)
        )
        if locked_environment.status != models.Environment.Status.READY:
            raise ValueError(
                f"Environment '{locked_environment.name}' is not ready for app operations "
                f"(status: {locked_environment.status})."
            )

        _raise_for_agent_limit(organization=organization, template=template)

        # The App row and its sandbox slug claim become visible together. Holding
        # the environment lock prevents teardown admission from interleaving.
        _raise_for_hostname_label_conflict(environment=locked_environment, app_slug=app_slug)
        sandbox_service.claim_sandbox_app_slug(
            app_slug=app_slug,
            organization_id=organization.id,
            environment=locked_environment,
        )
        app = models.App.objects.create(
            organization=organization,
            workspace=workspace,
            environment=locked_environment,
            source_template=template,
            name=app_name,
            slug=app_slug,
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
        _stamp_template_tags(
            organization=organization,
            app=app,
            template=template,
            owner_username=owner_username,
        )
        app = app_job_service.queue_deploy(app=app, created_by=created_by)

    logger.info(
        "Queued template deployment: app=%(app)s, template=%(template)s, environment=%(env)s",
        {"app": app_slug, "template": template.slug, "env": environment.slug},
    )

    return app
