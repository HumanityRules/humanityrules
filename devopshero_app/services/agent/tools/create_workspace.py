"""
Tool for creating workspaces.

This tool allows the agent to create new workspaces for organizing
applications and deployments within an organization.
"""

from dataclasses import dataclass, asdict

from django.utils.text import slugify

from devopshero_app.models import AWSAccount, Organization, User, Workspace


@dataclass
class WorkspaceSummary:
    """Summary of a created workspace."""

    id: str
    name: str
    slug: str
    description: str
    aws_account_id: str
    aws_account_name: str
    aws_region: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def create_workspace(
    name: str,
    aws_account_id: str,
    aws_region: str,
    organization: Organization,
    user: User,
    description: str = "",
    primary_repo_url: str = "",
) -> WorkspaceSummary:
    """
    Create a new workspace in the organization.

    Args:
        name: Human-readable name for the workspace.
        aws_account_id: UUID of the AWSAccount to use for deployments.
        aws_region: AWS region for deployments (e.g., us-east-1).
        organization: The Organization this workspace belongs to.
        user: The User creating the workspace.
        description: Optional description of the workspace.
        primary_repo_url: Optional primary repository URL (file:// only in v1).

    Returns:
        WorkspaceSummary with the created workspace details.

    Raises:
        ValueError: If AWS account doesn't exist or doesn't belong to org.
    """
    # Validate AWS account exists and belongs to the organization
    try:
        aws_account = AWSAccount.objects.get(
            id=aws_account_id,
            organization=organization,
        )
    except AWSAccount.DoesNotExist:
        raise ValueError(
            f"AWS account {aws_account_id} not found or doesn't belong to your organization."
        )

    # Check account is connected
    if aws_account.status != AWSAccount.Status.CONNECTED:
        raise ValueError(
            f"AWS account '{aws_account.name}' is not connected (status: {aws_account.status}). "
            "Please complete the AWS connection process first."
        )

    # Generate unique slug
    base_slug = slugify(name)
    slug = base_slug
    counter = 1
    while Workspace.objects.filter(organization=organization, slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Create the workspace
    workspace = Workspace.objects.create(
        organization=organization,
        name=name,
        slug=slug,
        description=description,
        primary_repo_url=primary_repo_url,
        aws_account=aws_account,
        aws_region=aws_region,
        created_by=user,
    )

    return WorkspaceSummary(
        id=str(workspace.id),
        name=workspace.name,
        slug=workspace.slug,
        description=workspace.description,
        aws_account_id=str(aws_account.id),
        aws_account_name=aws_account.name,
        aws_region=workspace.aws_region,
    )
