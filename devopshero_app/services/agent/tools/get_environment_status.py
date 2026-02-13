"""
Tool for getting environment provisioning status.

This tool retrieves the current status of an environment and recent log entries.
"""

from dataclasses import dataclass, asdict

from devopshero_app.models import Environment, EnvironmentLog, Organization


@dataclass
class EnvironmentLogEntry:
    """A single log entry from environment provisioning."""

    source: str
    level: str
    message: str
    created_at: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class EnvironmentStatus:
    """Current status of an environment."""

    id: str
    name: str
    slug: str
    status: str
    status_message: str
    shared_alb_hosted_zone: str | None
    vpc_stack_name: str | None
    cluster_stack_name: str | None
    aws_account_id: str
    aws_account_name: str
    recent_logs: list[EnvironmentLogEntry]

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        result["recent_logs"] = [log.to_dict() for log in self.recent_logs]
        return result


async def get_environment_status(
    environment_id: str,
    organization: Organization,
    log_limit: int,
) -> EnvironmentStatus:
    """
    Get current environment status and recent logs.

    Args:
        environment_id: UUID of the environment to check.
        organization: The Organization to validate access.
        log_limit: Maximum number of recent logs to return.

    Returns:
        EnvironmentStatus with current status and recent logs.

    Raises:
        ValueError: If environment doesn't exist or doesn't belong to org.
    """
    # Validate environment exists and belongs to organization
    try:
        environment = await Environment.objects.select_related(
            "aws_account",
        ).aget(
            id=environment_id,
            aws_account__organization=organization,
        )
    except Environment.DoesNotExist:
        raise ValueError(
            f"Environment {environment_id} not found or doesn't belong to your organization."
        )

    # Get recent logs (fetched newest first, then reversed to show oldest first)
    recent_logs_qs = EnvironmentLog.objects.filter(
        environment=environment,
    ).order_by("-created_at")[:log_limit]

    recent_logs_list = [log async for log in recent_logs_qs]

    recent_logs = [
        EnvironmentLogEntry(
            source=log.source,
            level=log.level,
            message=log.message,
            created_at=log.created_at.isoformat(),
        )
        for log in reversed(recent_logs_list)  # Reverse to show oldest first
    ]

    return EnvironmentStatus(
        id=str(environment.id),
        name=environment.name,
        slug=environment.slug,
        status=environment.status,
        status_message=environment.status_message,
        shared_alb_hosted_zone=environment.shared_alb_hosted_zone or None,
        vpc_stack_name=environment.vpc_stack_name or None,
        cluster_stack_name=environment.cluster_stack_name or None,
        aws_account_id=str(environment.aws_account.id),
        aws_account_name=environment.aws_account.name,
        recent_logs=recent_logs,
    )
