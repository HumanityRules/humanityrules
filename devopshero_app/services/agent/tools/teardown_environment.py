"""
Tool for tearing down (destroying) an entire environment.

Triggers deletion of all deployments in the environment, followed by
the base infrastructure (ECS cluster, builder, VPC CloudFormation stacks).
"""

from dataclasses import asdict, dataclass

from devopshero_app.models import (
    Environment,
    EnvironmentLog,
    Organization,
    User,
)


@dataclass
class EnvironmentTeardownSummary:
    """Summary of a queued environment teardown."""

    environment_id: str
    environment_name: str
    environment_slug: str
    aws_account_name: str
    previous_status: str
    status: str
    status_message: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def _create_teardown_log(environment: Environment) -> None:
    """Create initial teardown log entry."""
    params = {"env_name": environment.name}
    template = "Environment teardown queued for %(env_name)s"
    await EnvironmentLog.objects.acreate(
        environment=environment,
        level=EnvironmentLog.Level.INFO,
        message=template % params,
        details={
            "template": template,
            "params": params,
        },
    )


async def teardown_environment(
    environment_id: str,
    organization: Organization,
    user: User,
) -> EnvironmentTeardownSummary:
    """
    Queue teardown of an environment.

    This queues the environment for teardown. The job worker will:
    1. Tear down all deployments in the environment
    2. Delete the cluster/builder/VPC CloudFormation stacks

    Returns EnvironmentTeardownSummary with the queued teardown details.

    Raises ValueError if environment doesn't exist, doesn't belong to
    organization, or is not in a teardownable state.
    """
    # Validate environment exists and belongs to organization's AWS accounts
    try:
        environment = await (
            Environment.objects
            .select_related("aws_account", "aws_account__organization")
            .aget(
                id=environment_id,
                aws_account__organization=organization,
            )
        )
    except Environment.DoesNotExist:
        raise ValueError(
            f"Environment {environment_id} not found or doesn't belong to your organization."
        )

    # Check if environment is in a teardownable state
    if environment.status == Environment.Status.TEARDOWN_PENDING:
        raise ValueError(
            f"Environment '{environment.name}' is already queued for teardown."
        )
    if environment.status == Environment.Status.TEARING_DOWN:
        raise ValueError(
            f"Environment '{environment.name}' is currently being torn down."
        )

    previous_status = environment.status

    # Queue for teardown
    environment.status = Environment.Status.TEARDOWN_PENDING
    environment.status_message = "Teardown queued"
    await environment.asave(update_fields=["status", "status_message", "updated_at"])

    # Create teardown log entry
    await _create_teardown_log(environment=environment)

    return EnvironmentTeardownSummary(
        environment_id=str(environment.id),
        environment_name=environment.name,
        environment_slug=environment.slug,
        aws_account_name=environment.aws_account.name,
        previous_status=previous_status,
        status=environment.status,
        status_message=environment.status_message,
    )
