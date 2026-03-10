"""
Tool for triggering deployment from a DeploymentBlueprint.

Creates a Deployment record with PENDING status and sets the blueprint to DEPLOYING.
The job worker picks it up from there.
"""

from dataclasses import asdict, dataclass
from datetime import datetime

from devopshero_app.models import Conversation, Deployment, DeploymentBlueprint, DeploymentLog
from devopshero_app.services import deployment_blueprint_effective_values


@dataclass
class DeployBlueprintResult:
    """Result of deploy_blueprint operation."""

    deployment_id: str
    blueprint_id: str
    app_id: str
    app_name: str
    environment_name: str
    git_ref: str
    image_tag: str
    status: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def _generate_image_tag(app_slug: str, git_ref: str) -> str:
    """Generate a unique image tag for this deployment."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_ref = git_ref[:8] if len(git_ref) > 8 else git_ref
    return f"{app_slug}-{short_ref}-{timestamp}"


async def deploy_blueprint(conversation: Conversation) -> DeployBlueprintResult:
    """Trigger a deployment from the conversation's current blueprint."""
    if not conversation.context_deployment_blueprint_id:
        raise ValueError(
            "No blueprint context set. Use save_blueprint first to create a deployment blueprint."
        )

    blueprint = await DeploymentBlueprint.objects.select_related(
        "app", "app__repository", "environment",
    ).aget(id=conversation.context_deployment_blueprint_id)

    if blueprint.status not in (DeploymentBlueprint.Status.DRAFT, DeploymentBlueprint.Status.FAILED):
        raise ValueError(
            f"Blueprint is in '{blueprint.status}' state and cannot be deployed. "
            "Only draft or failed blueprints can be deployed."
        )

    active_statuses = [
        Deployment.Status.PENDING,
        Deployment.Status.BUILDING,
        Deployment.Status.PUSHING,
        Deployment.Status.DEPLOYING,
        Deployment.Status.STARTING,
    ]
    active_deployment = await Deployment.objects.filter(
        blueprint=blueprint,
        status__in=active_statuses,
    ).afirst()
    if active_deployment:
        raise ValueError(
            f"Blueprint already has an active deployment in progress "
            f"(status: {active_deployment.status}). Wait for it to complete."
        )

    effective_values = await deployment_blueprint_effective_values.aresolve_deployment_blueprint_effective_values(
        app=blueprint.app,
        blueprint=blueprint,
    )
    git_ref = effective_values.branch
    image_tag = _generate_image_tag(
        app_slug=blueprint.app.slug,
        git_ref=git_ref,
    )

    blueprint.status = DeploymentBlueprint.Status.DEPLOYING
    blueprint.status_message = "Deployment triggered"
    await blueprint.asave(update_fields=["status", "status_message", "updated_at"])

    deployment = await Deployment.objects.acreate(
        blueprint=blueprint,
        app=blueprint.app,
        environment=blueprint.environment,
        subdomain=effective_values.subdomain,
        git_ref=git_ref,
        git_commit_sha="",
        git_commit_message="",
        image_tag=image_tag,
        status=Deployment.Status.PENDING,
        status_message="Deployment queued",
        created_by=conversation.user,
    )

    params = {
        "app_name": blueprint.app.name,
        "git_ref": git_ref,
        "image_tag": image_tag,
        "environment_name": blueprint.environment.name,
    }
    template = "Deployment queued for %(app_name)s in %(environment_name)s (git_ref=%(git_ref)s, image_tag=%(image_tag)s)"
    await DeploymentLog.objects.acreate(
        deployment=deployment,
        level=DeploymentLog.Level.INFO,
        message=template % params,
        details={"template": template, "params": params},
    )

    return DeployBlueprintResult(
        deployment_id=str(deployment.id),
        blueprint_id=str(blueprint.id),
        app_id=str(blueprint.app_id),
        app_name=blueprint.app.name,
        environment_name=blueprint.environment.name,
        git_ref=git_ref,
        image_tag=image_tag,
        status=deployment.status,
    )
