"""
Tool for listing AWS accounts connected to the organization.

This tool allows the agent to see which AWS accounts are available
for deploying applications.
"""

from dataclasses import dataclass, asdict

from humanityrules_app.models import AWSAccount, Organization


@dataclass
class AWSAccountSummary:
    """Summary of a connected AWS account."""

    id: str
    name: str
    aws_account_id: str
    status: str
    region: str | None  # Default region if set

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def list_aws_accounts(organization: Organization) -> list[AWSAccountSummary]:
    """
    List AWS accounts connected to the organization.

    Args:
        organization: The Organization to query.

    Returns:
        List of AWSAccountSummary objects.
    """
    return [
        AWSAccountSummary(
            id=str(account.id),
            name=account.name,
            aws_account_id=account.aws_account_id or "",
            status=account.status,
            region=None,  # TODO: Add default region to AWSAccount model
        )
        async for account in AWSAccount.objects.filter(organization=organization)
    ]
