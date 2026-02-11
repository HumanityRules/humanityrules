"""
Tool for getting deployment status.

This tool retrieves the current status of a deployment and recent log entries.
"""

from dataclasses import dataclass, asdict

from devopshero_app.models import Deployment, DeploymentLog, Organization


@dataclass
class DeploymentLogEntry:
    """A single log entry from a deployment."""

    source: str
    level: str
    message: str
    created_at: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class DeploymentStatus:
    """Current status of a deployment."""

    id: str
    app_id: str
    app_name: str
    git_ref: str
    status: str
    status_message: str
    started_at: str | None
    completed_at: str | None
    service_url: str | None
    recent_logs: list[DeploymentLogEntry]

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        result["recent_logs"] = [log.to_dict() for log in self.recent_logs]
        return result


async def get_deployment_status(
    deployment_id: str,
    organization: Organization,
    log_limit: int,
) -> DeploymentStatus:
    """
    Get current deployment status and recent logs.

    Args:
        deployment_id: UUID of the deployment to check.
        organization: The Organization to validate access.
        log_limit: Maximum number of recent logs to return.

    Returns:
        DeploymentStatus with current status and recent logs.

    Raises:
        ValueError: If deployment doesn't exist or doesn't belong to org.
    """
    # Validate deployment exists and belongs to organization
    try:
        deployment = await Deployment.objects.select_related(
            "app",
            "app__workspace",
        ).aget(
            id=deployment_id,
            app__workspace__organization=organization,
        )
    except Deployment.DoesNotExist:
        raise ValueError(
            f"Deployment {deployment_id} not found or doesn't belong to your organization."
        )

    # Get recent logs (fetched newest first, then reversed to show oldest first)
    recent_logs_qs = DeploymentLog.objects.filter(
        deployment=deployment,
    ).order_by("-created_at")[:log_limit]

    recent_logs_list = [log async for log in recent_logs_qs]

    recent_logs = [
        DeploymentLogEntry(
            source=log.source,
            level=log.level,
            message=log.message,
            created_at=log.created_at.isoformat(),
        )
        for log in reversed(recent_logs_list)  # Reverse to show oldest first
    ]

    return DeploymentStatus(
        id=str(deployment.id),
        app_id=str(deployment.app.id),
        app_name=deployment.app.name,
        git_ref=deployment.git_ref,
        status=deployment.status,
        status_message=deployment.status_message,
        started_at=deployment.started_at.isoformat() if deployment.started_at else None,
        completed_at=deployment.completed_at.isoformat() if deployment.completed_at else None,
        service_url=deployment.service_url or None,
        recent_logs=recent_logs,
    )
