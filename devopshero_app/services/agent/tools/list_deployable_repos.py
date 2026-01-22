"""
Tool for listing available repositories for deployment.

This tool scans the deployable_repos/ folder and returns available
repositories as file:// URLs that can be used with inspect_repository.
"""

from dataclasses import dataclass, asdict
from pathlib import Path

from django.conf import settings


@dataclass
class DeployableRepoSummary:
    """Summary of an available repository for deployment."""

    name: str  # Folder name (e.g., "simple_dashboard")
    url: str  # file:// URL (e.g., "file:///path/to/deployable_repos/simple_dashboard")
    description: str  # From README.md first line, or empty

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def _extract_description(repo_path: Path) -> str:
    """Extract description from README.md if present."""
    readme_path = repo_path / "README.md"
    if not readme_path.exists():
        return ""

    try:
        lines = readme_path.read_text().splitlines()
        for line in lines:
            line = line.strip()
            # Skip empty lines and markdown headings
            if not line or line.startswith("#"):
                continue
            # Return first non-heading, non-empty line
            return line
    except (UnicodeDecodeError, PermissionError):
        pass

    return ""


def list_deployable_repos() -> list[DeployableRepoSummary]:
    """
    List available repositories for deployment.

    Scans the deployable_repos/ folder and returns each subfolder
    as a deployable repository with a file:// URL.

    Returns:
        List of DeployableRepoSummary objects.
    """
    deployable_repos_dir = settings.BASE_DIR / "deployable_repos"

    if not deployable_repos_dir.exists():
        return []

    repos = []
    public_repos_dir = deployable_repos_dir / "public"
    for entry in sorted(deployable_repos_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Skip hidden directories
        if entry.name.startswith("."):
            continue
        if entry.name == "public":
            continue

        repos.append(
            DeployableRepoSummary(
                name=entry.name,
                url=f"file://{entry.resolve()}",
                description=_extract_description(repo_path=entry),
            )
        )

    if public_repos_dir.exists():
        for entry in sorted(public_repos_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue

            repos.append(
                DeployableRepoSummary(
                    name=f"public/{entry.name}",
                    url=f"file://{entry.resolve()}",
                    description=_extract_description(repo_path=entry),
                )
            )

    return repos
