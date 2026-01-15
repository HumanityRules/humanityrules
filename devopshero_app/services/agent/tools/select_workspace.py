"""
Tool for selecting a workspace for a conversation.

This tool pins a workspace to the current conversation. Once pinned,
the workspace cannot be changed for that conversation.
"""

from dataclasses import dataclass, asdict

from devopshero_app.models import Conversation, Organization, Workspace


@dataclass
class SelectedWorkspaceSummary:
    """Summary of the selected workspace."""

    id: str
    name: str
    slug: str
    description: str
    primary_repo_url: str
    aws_account_id: str
    aws_account_name: str
    aws_region: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def select_workspace(workspace_id: str, conversation: Conversation, organization: Organization) -> SelectedWorkspaceSummary:
    """
    Pin a workspace to this conversation.

    Once a workspace is selected, it cannot be changed for this conversation.
    All subsequent workspace-scoped operations will use this workspace.

    Args:
        workspace_id: UUID of the workspace to select.
        conversation: The current Conversation to pin the workspace to.
        organization: The Organization (for validation).

    Returns:
        SelectedWorkspaceSummary with the workspace details.

    Raises:
        ValueError: If workspace is already pinned or doesn't exist.
    """
    # Check if workspace is already pinned
    if conversation.workspace is not None:
        raise ValueError(
            f"This conversation is already pinned to workspace '{conversation.workspace.name}'. "
            "Start a new conversation to work with a different workspace."
        )

    # Fetch the workspace with related AWS account
    try:
        workspace = await Workspace.objects.select_related("aws_account").aget(
            id=workspace_id,
            organization=organization,
        )
    except Workspace.DoesNotExist:
        raise ValueError(
            f"Workspace {workspace_id} not found or doesn't belong to your organization."
        )

    # Pin the workspace to the conversation and update title
    conversation.workspace = workspace
    conversation.title = f"Working on {workspace.name}"
    await conversation.asave()

    return SelectedWorkspaceSummary(
        id=str(workspace.id),
        name=workspace.name,
        slug=workspace.slug,
        description=workspace.description,
        primary_repo_url=workspace.primary_repo_url,
        aws_account_id=str(workspace.aws_account.id),
        aws_account_name=workspace.aws_account.name,
        aws_region=workspace.aws_region,
    )
