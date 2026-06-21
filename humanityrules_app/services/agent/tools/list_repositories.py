"""
Tool for listing repositories connected to an organization.

This tool allows the agent to see available repositories that can be
used for app deployments.
"""

from dataclasses import dataclass, asdict

from humanityrules_app.models import Organization, Repository


@dataclass
class RepositoryListItem:
    """Summary of a repository for listing."""

    id: str
    name: str
    full_name: str
    clone_url: str
    default_branch: str
    provider: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def list_repositories(organization: Organization) -> list[RepositoryListItem]:
    """
    List all repositories connected to the organization.

    Args:
        organization: The Organization to query.

    Returns:
        List of RepositoryListItem objects.
    """
    repositories = []
    async for repo in Repository.objects.filter(organization=organization).order_by("full_name"):
        repositories.append(
            RepositoryListItem(
                id=str(repo.id),
                name=repo.name,
                full_name=repo.full_name,
                clone_url=repo.clone_url,
                default_branch=repo.default_branch,
                provider=repo.provider,
            )
        )
    return repositories
