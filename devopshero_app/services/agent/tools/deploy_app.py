"""
Tool for deploying applications.

Creates a Deployment record with PENDING status. The deployment worker
picks up pending deployments and executes them via the CDK infrastructure.
"""

from dataclasses import asdict, dataclass
from datetime import datetime

from devopshero_app.models import (
    App,
    Deployment,
    DeploymentLog,
    Environment,
    Organization,
    User,
)


@dataclass
class DeploymentSummary:
    """Summary of a created deployment."""

    id: str
    app_id: str
    app_name: str
    environment_name: str
    git_ref: str
    status: str
    status_message: str
    image_tag: str
    created_at: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def _generate_image_tag(app: App, git_ref: str) -> str:
    """Generate a unique image tag for this deployment."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_ref = git_ref[:8] if len(git_ref) > 8 else git_ref
    return f"{app.slug}-{short_ref}-{timestamp}"


async def _create_initial_logs(deployment: Deployment) -> None:
    """Create initial deployment log entries."""
    params = {
        "app_name": deployment.app.name,
        "git_ref": deployment.git_ref,
        "image_tag": deployment.image_tag,
        "environment_name": deployment.environment.name,
    }
    template = "Deployment queued for %(app_name)s in %(environment_name)s (git_ref=%(git_ref)s, image_tag=%(image_tag)s)"
    await DeploymentLog.objects.acreate(
        deployment=deployment,
        level=DeploymentLog.Level.INFO,
        message=template % params,
        details={
            "template": template,
            "params": params,
        },
    )


async def deploy_app(
    app_id: str,
    git_ref: str,
    organization: Organization,
    user: User,
    environment_slug: str,
) -> DeploymentSummary:
    """
    Create a deployment for an application.

    Creates a Deployment record with status PENDING. The deployment worker
    picks it up and executes the actual CDK deployment.

    Args:
        app_id: UUID of the App to deploy.
        git_ref: Git reference (branch, tag, or commit SHA) to deploy.
        organization: The Organization this deployment belongs to.
        user: The User initiating the deployment.
        environment_slug: Target environment slug (e.g., "default").

    Returns:
        DeploymentSummary with the created deployment details.

    Raises:
        ValueError: If app doesn't exist, doesn't belong to organization,
                    or environment not found.
    """
    # Validate app exists and belongs to organization
    try:
        app = await App.objects.select_related(
            "workspace",
            "workspace__aws_account",
        ).aget(
            id=app_id,
            workspace__organization=organization,
        )
    except App.DoesNotExist:
        raise ValueError(
            f"App {app_id} not found or doesn't belong to your organization."
        )

    # Get target environment
    environment = await Environment.objects.filter(
        aws_account=app.workspace.aws_account,
        slug=environment_slug,
    ).afirst()
    if not environment:
        raise ValueError(
            f"Environment '{environment_slug}' not found for this workspace's AWS account."
        )

    # Check for existing active deployments
    active_statuses = [
        Deployment.Status.PENDING,
        Deployment.Status.BUILDING,
        Deployment.Status.PUSHING,
        Deployment.Status.DEPLOYING,
        Deployment.Status.STARTING,
    ]
    active_deployment = await Deployment.objects.filter(
        app=app,
        status__in=active_statuses,
    ).afirst()

    if active_deployment:
        raise ValueError(
            f"App '{app.name}' already has an active deployment in progress "
            f"(status: {active_deployment.status}). Please wait for it to complete."
        )

    # Generate image tag
    image_tag = _generate_image_tag(app, git_ref)

    # Create deployment record
    deployment = await Deployment.objects.acreate(
        app=app,
        environment=environment,
        git_ref=git_ref,
        git_commit_sha="",  # Would be resolved from git
        git_commit_message="",  # Would be fetched from git
        image_tag=image_tag,
        status=Deployment.Status.PENDING,
        status_message="Deployment queued",
        created_by=user,
    )

    # Create initial log entries
    await _create_initial_logs(deployment)

    return DeploymentSummary(
        id=str(deployment.id),
        app_id=str(app.id),
        app_name=app.name,
        environment_name=environment.name,
        git_ref=deployment.git_ref,
        status=deployment.status,
        status_message=deployment.status_message,
        image_tag=deployment.image_tag,
        created_at=deployment.created_at.isoformat(),
    )
