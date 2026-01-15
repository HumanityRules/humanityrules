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
    primary_repo_url: str
    aws_account_name: str
    aws_region: str
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
    ).select_related("aws_account").prefetch_related("apps"):
        workspaces.append(
            WorkspaceListItem(
                id=str(ws.id),
                name=ws.name,
                slug=ws.slug,
                description=ws.description,
                primary_repo_url=ws.primary_repo_url,
                aws_account_name=ws.aws_account.name,
                aws_region=ws.aws_region,
                app_count=await ws.apps.acount(),
            )
        )
    return workspaces
