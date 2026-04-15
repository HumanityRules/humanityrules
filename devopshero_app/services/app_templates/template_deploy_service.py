"""
Deploy an application from an AppTemplate.

Creates the full record chain (Repository, App, DeploymentBlueprint, Deployment)
and queues the deployment for the job worker.
"""

import logging
from datetime import datetime

from django.conf import settings

from devopshero_app import models
from devopshero_app.services import deployment_blueprint_effective_values

logger = logging.getLogger(__name__)


def _generate_image_tag(app_slug: str, git_ref: str) -> str:
    """Generate a unique image tag for this deployment."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_ref = git_ref[:8] if len(git_ref) > 8 else git_ref
    return f"{app_slug}-{short_ref}-{timestamp}"


def _materialize_environment_variables(runtime_variables: list[dict]) -> list[dict[str, str]]:
    """Extract config vars from runtime_variables into blueprint environment_variables format."""
    env_vars = []
    for var in runtime_variables:
        if var["category"] != "config":
            continue
        value = var.get("value", "")
        if value:
            env_vars.append({"name": var["name"], "value": value})
    return env_vars


def _materialize_app_secrets(runtime_variables: list[dict]) -> dict[str, str | None]:
    """Extract secret vars from runtime_variables into blueprint app_secrets format."""
    secrets = {}
    for var in runtime_variables:
        if var["category"] != "secret":
            continue
        # None = auto-generate, "" = empty placeholder, "literal" = use as-is
        secrets[var["name"]] = var.get("value")
    return secrets


async def deploy_from_template(
    template: models.AppTemplate,
    organization: models.Organization,
    workspace: models.Workspace,
    environment: models.Environment,
    app_name: str,
    app_slug: str,
    created_by: models.User,
) -> models.Deployment:
    """Create Repository + App + Blueprint + Deployment from a template and queue for deployment."""
    clone_url = f"file://{settings.TEMPLATE_REPOS_DIR / template.source_repo_path}"

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

    app = await models.App.objects.acreate(
        organization=organization,
        workspace=workspace,
        repository=repo,
        source_template=template,
        name=app_name,
        slug=app_slug,
        app_type=template.app_type,
        build_strategy=template.build_strategy,
        dockerfile_path=template.dockerfile_path,
        container_port=template.container_port,
        health_check_path=template.health_check_path,
        health_check_command=template.health_check_command,
        health_check_grace_period=template.health_check_grace_period,
        branch="",
        created_by=created_by,
    )

    environment_variables = _materialize_environment_variables(template.runtime_variables)
    app_secrets = _materialize_app_secrets(template.runtime_variables)

    blueprint = await models.DeploymentBlueprint.objects.acreate(
        app=app,
        environment=environment,
        status=models.DeploymentBlueprint.Status.DEPLOYING,
        status_message="Deployment triggered from template",
        cpu=template.cpu,
        memory=template.memory,
        environment_variables=environment_variables,
        app_secrets=app_secrets if app_secrets else None,
        subdomain="",
        created_by=created_by,
    )

    effective_values = await deployment_blueprint_effective_values.aresolve_deployment_blueprint_effective_values(
        app=app,
        blueprint=blueprint,
    )
    git_ref = effective_values.branch
    image_tag = _generate_image_tag(app_slug=app_slug, git_ref=git_ref)

    deployment = await models.Deployment.objects.acreate(
        blueprint=blueprint,
        app=app,
        environment=environment,
        subdomain=effective_values.subdomain,
        git_ref=git_ref,
        git_commit_sha="",
        git_commit_message="",
        image_tag=image_tag,
        status=models.Deployment.Status.PENDING,
        status_message="Deployment queued from template",
        created_by=created_by,
    )

    logger.info(
        "Queued template deployment: app=%(app)s, template=%(template)s, environment=%(env)s",
        {"app": app_slug, "template": template.slug, "env": environment.slug},
    )

    return deployment
