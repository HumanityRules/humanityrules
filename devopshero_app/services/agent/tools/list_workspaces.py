"""
Tool for listing workspaces in an organization.

This tool allows the agent to see available workspaces that can be
selected for deployment operations.
"""

from dataclasses import dataclass, asdict

from devopshero_app.models import Organization, Workspace


@dataclass
class WorkspaceListItem:
    """Summary of a workspace for listing."""

    id: str
    name: str
    slug: str
    description: str
    app_count: int

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def list_workspaces(organization: Organization) -> list[WorkspaceListItem]:
    """
    List all workspaces in the organization.

    Args:
        organization: The Organization to query.

    Returns:
        List of WorkspaceListItem objects.
    """
    workspaces = []
    async for ws in Workspace.objects.filter(
        organization=organization
    ).prefetch_related("apps"):
        workspaces.append(
            WorkspaceListItem(
                id=str(ws.id),
                name=ws.name,
                slug=ws.slug,
                description=ws.description,
                app_count=await ws.apps.acount(),
            )
        )
    return workspaces
