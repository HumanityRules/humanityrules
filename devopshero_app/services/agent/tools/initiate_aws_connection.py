"""
Tool for initiating AWS account connection.

This tool allows the agent to start the AWS account connection process
by creating a pending AWSAccount record and returning the CloudFormation URL.
"""

from dataclasses import dataclass, asdict

from devopshero_app.models import AWSAccount, Organization, User


@dataclass
class AWSConnectionInfo:
    """Information for completing AWS account connection."""

    account_id: str
    account_name: str
    cloudformation_url: str
    instructions: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def initiate_aws_connection(account_name: str, organization: Organization, user: User) -> AWSConnectionInfo:
    """
    Create a pending AWS account and return the CloudFormation quick-create URL.

    This starts the AWS connection flow. The user must click the CloudFormation
    link to deploy the stack in their AWS account. Once deployed, the Lambda
    callback will mark the account as connected.

    Args:
        account_name: User-friendly name for this AWS account (e.g., 'Production', 'Staging').
        organization: The Organization this account belongs to.
        user: The User initiating the connection.

    Returns:
        AWSConnectionInfo with the CloudFormation URL and instructions.
    """
    aws_account = await AWSAccount.objects.acreate(
        organization=organization,
        name=account_name,
        status=AWSAccount.Status.PENDING,
        created_by=user,
    )

    return AWSConnectionInfo(
        account_id=str(aws_account.id),
        account_name=aws_account.name,
        cloudformation_url=aws_account.get_cloudformation_url(),
        instructions=(
            "Click the link above to open AWS Console. "
            "Review the CloudFormation stack and click 'Create stack'. "
            "Once complete, your AWS account will be automatically connected."
        ),
    )
