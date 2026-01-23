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
    # Check if workspace is already pinned (use context_workspace_id to avoid lazy load)
    if conversation.context_workspace_id is not None:
        raise ValueError(
            "This conversation is already pinned to a workspace. "
            "Start a new conversation to work with a different workspace."
        )

    # Fetch the workspace
    try:
        workspace = await Workspace.objects.aget(
            id=workspace_id,
            organization=organization,
        )
    except Workspace.DoesNotExist:
        raise ValueError(
            f"Workspace {workspace_id} not found or doesn't belong to your organization."
        )

    # Pin the workspace to the conversation and update title
    conversation.context_workspace = workspace
    conversation.title = f"Working on {workspace.name}"
    await conversation.asave()

    return SelectedWorkspaceSummary(
        id=str(workspace.id),
        name=workspace.name,
        slug=workspace.slug,
        description=workspace.description,
    )
