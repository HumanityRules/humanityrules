"""
Tool for creating workspaces.

This tool allows the agent to create new workspaces for organizing
applications and deployments within an organization.
"""

from dataclasses import dataclass, asdict

from django.utils.text import slugify

from devopshero_app.models import Organization, User, Workspace


@dataclass
class WorkspaceSummary:
    """Summary of a created workspace."""

    id: str
    name: str
    slug: str
    description: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def create_workspace(
    name: str,
    organization: Organization,
    user: User,
    description: str,
) -> WorkspaceSummary:
    """
    Create a new workspace in the organization.

    Args:
        name: Human-readable name for the workspace.
        organization: The Organization this workspace belongs to.
        user: The User creating the workspace.
        description: Description of the workspace.

    Returns:
        WorkspaceSummary with the created workspace details.
    """
    # Generate unique slug
    base_slug = slugify(name)
    slug = base_slug
    counter = 1
    while await Workspace.objects.filter(organization=organization, slug=slug).aexists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Create the workspace
    workspace = await Workspace.objects.acreate(
        organization=organization,
        name=name,
        slug=slug,
        description=description,
        created_by=user,
    )

    return WorkspaceSummary(
        id=str(workspace.id),
        name=workspace.name,
        slug=workspace.slug,
        description=workspace.description,
    )
