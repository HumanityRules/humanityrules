"""
Tool for deploying applications.

This tool creates deployment records and simulates deployment progress.
In v1, this is stubbed - it does NOT trigger real infrastructure.
Future v2 will connect to the infra_customer/ deployment engine.
"""

import uuid
from dataclasses import dataclass, asdict
from datetime import datetime

from django.utils import timezone

from devopshero_app.models import (
    App,
    Conversation,
    Deployment,
    DeploymentLog,
    Organization,
    User,
)


@dataclass
class DeploymentSummary:
    """Summary of a created deployment."""

    id: str
    app_id: str
    app_name: str
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
    await DeploymentLog.objects.acreate(
        deployment=deployment,
        phase=DeploymentLog.Phase.INIT,
        level=DeploymentLog.Level.INFO,
        message="Deployment initiated",
        details={
            "app_name": deployment.app.name,
            "git_ref": deployment.git_ref,
            "image_tag": deployment.image_tag,
        },
    )


async def deploy_app(
    app_id: str,
    git_ref: str,
    organization: Organization,
    user: User,
    conversation: Conversation | None,
) -> DeploymentSummary:
    """
    Create a deployment for an application.

    In v1 (stubbed):
    - Creates a Deployment record with status "pending"
    - Creates initial log entries
    - Does NOT trigger real infrastructure deployment

    Future v2 will trigger real infrastructure deployment.

    Args:
        app_id: UUID of the App to deploy.
        git_ref: Git reference (branch, tag, or commit SHA) to deploy.
        organization: The Organization this deployment belongs to.
        user: The User initiating the deployment.
        conversation: Conversation that triggered this deployment.

    Returns:
        DeploymentSummary with the created deployment details.

    Raises:
        ValueError: If app doesn't exist or doesn't belong to organization.
    """
    # Validate app exists and belongs to organization
    try:
        app = await App.objects.select_related("workspace").aget(
            id=app_id,
            workspace__organization=organization,
        )
    except App.DoesNotExist:
        raise ValueError(
            f"App {app_id} not found or doesn't belong to your organization."
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
        conversation=conversation,
        git_ref=git_ref,
        git_commit_sha="",  # Would be resolved in v2
        git_commit_message="",  # Would be fetched in v2
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
        git_ref=deployment.git_ref,
        status=deployment.status,
        status_message=deployment.status_message,
        image_tag=deployment.image_tag,
        created_at=deployment.created_at.isoformat(),
    )


def simulate_deployment_progress(deployment_id: str) -> None:
    """
    Simulate deployment progress for demo purposes.

    This is a helper function that advances a deployment through
    its phases. In v1, this is called to simulate progress.
    In v2, real deployment would update these statuses.

    Args:
        deployment_id: UUID of the deployment to advance.
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)
    except Deployment.DoesNotExist:
        return

    # Define the progression of phases
    phase_progression = [
        (Deployment.Status.BUILDING, DeploymentLog.Phase.BUILD, "Building Docker image..."),
        (Deployment.Status.PUSHING, DeploymentLog.Phase.PUSH, "Pushing image to ECR..."),
        (Deployment.Status.DEPLOYING, DeploymentLog.Phase.DEPLOY, "Deploying infrastructure..."),
        (Deployment.Status.STARTING, DeploymentLog.Phase.HEALTH, "Starting service and running health checks..."),
        (Deployment.Status.RUNNING, DeploymentLog.Phase.COMPLETE, "Deployment completed successfully"),
    ]

    # Find current position and advance to next
    current_statuses = [p[0] for p in phase_progression]
    try:
        current_idx = current_statuses.index(deployment.status)
        if current_idx < len(phase_progression) - 1:
            next_status, next_phase, next_message = phase_progression[current_idx + 1]
        else:
            return  # Already at final state
    except ValueError:
        # Start from beginning if status is PENDING
        if deployment.status == Deployment.Status.PENDING:
            next_status, next_phase, next_message = phase_progression[0]
            deployment.started_at = timezone.now()
        else:
            return

    # Update deployment status
    deployment.status = next_status
    deployment.status_message = next_message

    if next_status == Deployment.Status.RUNNING:
        deployment.completed_at = timezone.now()
        # Set simulated service URL
        deployment.service_url = f"https://{deployment.app.slug}.devopshero.app"
        deployment.alb_dns = f"{deployment.app.slug}-alb-123456.us-east-1.elb.amazonaws.com"

    deployment.save()

    # Create log entry
    DeploymentLog.objects.create(
        deployment=deployment,
        phase=next_phase,
        level=DeploymentLog.Level.INFO,
        message=next_message,
    )
