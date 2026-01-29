"""
GitHub App client for authentication and repository access.

Handles JWT generation, installation token exchange, and repository listing.
"""

import logging
import time
from dataclasses import dataclass

import httpx
import jwt
from django.conf import settings

from devopshero_app.models import GitProviderIntegration, Organization, Repository

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


@dataclass
class GitHubRepo:
    """Repository data from GitHub API."""

    id: int
    name: str
    full_name: str
    clone_url: str
    default_branch: str


@dataclass
class SyncResult:
    """Result of repository sync operation."""

    added: int
    updated: int
    removed: int


def generate_jwt() -> str:
    """Generate a JWT for GitHub App authentication (valid for 10 minutes)."""
    now = int(time.time())
    payload = {
        "iat": now - 60,  # Issued 60 seconds ago (clock skew tolerance)
        "exp": now + (10 * 60),  # Expires in 10 minutes
        "iss": settings.GITHUB_APP_ID,
    }
    return jwt.encode(
        payload=payload,
        key=settings.GITHUB_APP_PRIVATE_KEY,
        algorithm="RS256",
    )


def get_installation_token(installation_id: str) -> str:
    """Exchange JWT for an installation access token (valid for 1 hour)."""
    app_jwt = generate_jwt()

    response = httpx.post(
        url=f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    response.raise_for_status()

    data = response.json()
    return data["token"]


def get_installation_details(installation_id: str) -> dict:
    """Get details about a GitHub App installation."""
    app_jwt = generate_jwt()

    response = httpx.get(
        url=f"{GITHUB_API_BASE}/app/installations/{installation_id}",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    response.raise_for_status()

    return response.json()


def list_installation_repos(installation_id: str) -> list[GitHubRepo]:
    """Fetch all repositories accessible to the installation."""
    token = get_installation_token(installation_id=installation_id)
    repos: list[GitHubRepo] = []
    page = 1
    per_page = 100

    while True:
        response = httpx.get(
            url=f"{GITHUB_API_BASE}/installation/repositories",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"page": page, "per_page": per_page},
        )
        response.raise_for_status()

        data = response.json()
        for repo in data.get("repositories", []):
            repos.append(
                GitHubRepo(
                    id=repo["id"],
                    name=repo["name"],
                    full_name=repo["full_name"],
                    clone_url=repo["clone_url"],
                    default_branch=repo.get("default_branch", "main"),
                )
            )

        # Check if there are more pages
        if len(data.get("repositories", [])) < per_page:
            break
        page += 1

    return repos


def sync_repositories(organization: Organization, integration: GitProviderIntegration) -> SyncResult:
    """Sync repositories from GitHub to the database."""
    github_repos = list_installation_repos(installation_id=integration.installation_id)

    added = 0
    updated = 0

    # Get existing repos for this integration
    existing_repos = {
        repo.external_id: repo for repo in Repository.objects.filter(integration=integration)
    }

    github_repo_ids = set()

    for gh_repo in github_repos:
        external_id = str(gh_repo.id)
        github_repo_ids.add(external_id)

        if external_id in existing_repos:
            # Update existing repo
            repo = existing_repos[external_id]
            changed = False

            if repo.name != gh_repo.name:
                repo.name = gh_repo.name
                changed = True
            if repo.full_name != gh_repo.full_name:
                repo.full_name = gh_repo.full_name
                changed = True
            if repo.clone_url != gh_repo.clone_url:
                repo.clone_url = gh_repo.clone_url
                changed = True
            if repo.default_branch != gh_repo.default_branch:
                repo.default_branch = gh_repo.default_branch
                changed = True

            if changed:
                repo.save()
                updated += 1
        else:
            # Create new repo
            Repository.objects.create(
                organization=organization,
                integration=integration,
                provider=Repository.Provider.GITHUB,
                external_id=external_id,
                name=gh_repo.name,
                full_name=gh_repo.full_name,
                clone_url=gh_repo.clone_url,
                default_branch=gh_repo.default_branch,
            )
            added += 1

    # Remove repos that are no longer accessible
    removed = 0
    for external_id, repo in existing_repos.items():
        if external_id not in github_repo_ids:
            repo.delete()
            removed += 1

    logger.info(
        "Synced repositories for %s: added=%d, updated=%d, removed=%d",
        organization.name,
        added,
        updated,
        removed,
    )

    return SyncResult(added=added, updated=updated, removed=removed)


def get_app_installation_url() -> str:
    """Get the URL for installing the GitHub App."""
    return "https://github.com/apps/devops-hero-app/installations/new"
