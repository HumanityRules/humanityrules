"""
Tool for listing environments in an AWS account.

This tool allows the agent to discover existing environments before deploying apps.
"""

from dataclasses import dataclass, asdict

from devopshero_app.models import AWSAccount, Environment, Organization


@dataclass
class EnvironmentSummary:
    """Summary of an environment."""

    id: str
    name: str
    slug: str
    status: str
    shared_alb_hosted_zone: str | None
    aws_account_id: str
    aws_account_name: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def list_environments(aws_account_uuid: str, organization: Organization) -> list[EnvironmentSummary]:
    """
    List environments in an AWS account.

    Returns all environments with their status (PENDING, PROVISIONING, READY, ERROR).
    Use this to discover existing environments before deploying.

    Agent decision logic:
    - If "default" exists and is READY -> use it
    - If no environments exist -> create one (ask user for hosted zone first)
    - If multiple exist -> ask user which one to use

    Args:
        aws_account_uuid: Internal UUID of the AWSAccount record (not the 12-digit AWS account number).
        organization: The Organization (for access validation).

    Returns:
        List of EnvironmentSummary objects.

    Raises:
        ValueError: If AWS account not found or not connected.
    """
    # Validate AWS account exists and belongs to organization
    try:
        aws_account = await AWSAccount.objects.aget(
            id=aws_account_uuid,
            organization=organization,
        )
    except AWSAccount.DoesNotExist:
        raise ValueError(
            f"AWS account {aws_account_uuid} not found or doesn't belong to your organization."
        )

    # Check account is connected
    if aws_account.status != AWSAccount.Status.CONNECTED:
        raise ValueError(
            f"AWS account '{aws_account.name}' is not connected (status: {aws_account.status}). "
            "Please complete the AWS account connection first."
        )

    # Get all environments for this AWS account
    environments = []
    async for env in Environment.objects.filter(
        aws_account=aws_account,
    ).exclude(
        status=Environment.Status.DISCARDED,
    ).order_by("name"):
        environments.append(
            EnvironmentSummary(
                id=str(env.id),
                name=env.name,
                slug=env.slug,
                status=env.status,
                shared_alb_hosted_zone=env.shared_alb_hosted_zone or None,
                aws_account_id=str(aws_account.id),
                aws_account_name=aws_account.name,
            )
        )

    return environments
