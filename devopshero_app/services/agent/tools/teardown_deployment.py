"""
Tool for tearing down (destroying) a deployed application.

Triggers deletion of app-specific CDK stacks: ECS service, ALB rules,
Aurora database (if any), and ECR repository.
"""

from dataclasses import asdict, dataclass

from devopshero_app.models import (
    App,
    Deployment,
    DeploymentLog,
    Organization,
    User,
)


@dataclass
class TeardownSummary:
    """Summary of a queued teardown."""

    deployment_id: str
    app_id: str
    app_name: str
    environment_name: str
    previous_status: str
    status: str
    status_message: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def _create_teardown_log(deployment: Deployment) -> None:
    """Create initial teardown log entry."""
    params = {
        "app_name": deployment.app.name,
        "environment_name": deployment.environment.name,
    }
    template = "Teardown queued for %(app_name)s in %(environment_name)s"
    await DeploymentLog.objects.acreate(
        deployment=deployment,
        level=DeploymentLog.Level.INFO,
        message=template % params,
        details={
            "template": template,
            "params": params,
        },
    )


async def teardown_deployment(
    app_id: str,
    organization: Organization,
    user: User,
) -> TeardownSummary:
    """
    Queue teardown of an application's deployment.

    Finds the most recent deployment for the app and queues it for teardown.
    The job worker will delete the app's CDK stacks (ECS, ALB rules, Aurora, ECR).

    Args:
        app_id: UUID of the App to tear down.
        organization: The Organization this app belongs to.
        user: The User initiating the teardown.

    Returns:
        TeardownSummary with the queued teardown details.

    Raises:
        ValueError: If app doesn't exist, doesn't belong to organization,
                    has no deployments, or is not in a teardownable state.
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

    # Find the most recent deployment for this app
    deployment = await (
        Deployment.objects
        .filter(app=app)
        .select_related("app", "environment")
        .order_by("-created_at")
        .afirst()
    )

    if not deployment:
        raise ValueError(
            f"App '{app.name}' has no deployments to tear down."
        )

    # Check if deployment is in a teardownable state
    teardownable_statuses = [
        Deployment.Status.DEPLOYED,
        Deployment.Status.FAILED,
    ]
    if deployment.status not in teardownable_statuses:
        if deployment.status == Deployment.Status.TEARDOWN_PENDING:
            raise ValueError(
                f"App '{app.name}' is already queued for teardown."
            )
        if deployment.status == Deployment.Status.TEARING_DOWN:
            raise ValueError(
                f"App '{app.name}' is currently being torn down."
            )
        # In-progress deployment states
        raise ValueError(
            f"App '{app.name}' cannot be torn down while deployment is in progress "
            f"(status: {deployment.status}). Wait for deployment to complete."
        )

    previous_status = deployment.status

    # Queue for teardown
    deployment.status = Deployment.Status.TEARDOWN_PENDING
    deployment.status_message = "Teardown queued"
    await deployment.asave(update_fields=["status", "status_message", "updated_at"])

    # Create teardown log entry
    await _create_teardown_log(deployment)

    return TeardownSummary(
        deployment_id=str(deployment.id),
        app_id=str(app.id),
        app_name=app.name,
        environment_name=deployment.environment.name,
        previous_status=previous_status,
        status=deployment.status,
        status_message=deployment.status_message,
    )
