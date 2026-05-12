"""
Repository cloning service.

Handles cloning repositories from GitHub (with installation token auth)
and local file:// URLs for backward compatibility.
"""

import logging
import shutil
import subprocess
from pathlib import Path

from django.conf import settings

from devopshero_app.models import Repository

from . import github_client

logger = logging.getLogger(__name__)


def clone_repository(repository: Repository, branch: str, target_dir: Path) -> None:
    """
    Clone a repository to a specified directory.

    For GitHub repositories, uses installation token authentication.
    For local file:// URLs, copies the directory contents.

    If target_dir already exists, cloning is skipped (re-uses existing clone).
    """
    clone_url = repository.clone_url

    # Re-use existing clone if present (e.g., subsequent messages in same conversation)
    if target_dir.exists():
        logger.info(
            "Re-using existing clone at %(target)s",
            {"target": str(target_dir)},
        )
        return

    # Template-backed apps: resolve against the *worker's* TEMPLATE_REPOS_DIR,
    # so git worktrees build from their own template_repos/ instead of the
    # main checkout that originally created the app.
    if clone_url.startswith("doh-template://"):
        rel_path = clone_url[len("doh-template://"):]
        source_path = settings.TEMPLATE_REPOS_DIR / rel_path
        if not source_path.exists():
            raise ValueError(f"Template repository path does not exist: {source_path}")

        logger.info(
            "Copying template repository %(source)s to %(target)s",
            {"source": str(source_path), "target": str(target_dir)},
        )

        shutil.copytree(src=source_path, dst=target_dir, dirs_exist_ok=False)
        return

    # Handle local file:// URLs (backward compatibility with deployable_repos/)
    if clone_url.startswith("file://"):
        source_path = Path(clone_url[7:])
        if not source_path.exists():
            raise ValueError(f"Local repository path does not exist: {source_path}")

        logger.info(
            "Copying local repository %(source)s to %(target)s",
            {"source": str(source_path), "target": str(target_dir)},
        )

        # Copy directory contents to target
        shutil.copytree(src=source_path, dst=target_dir, dirs_exist_ok=False)
        return

    # For GitHub repos, we need the integration to get a token
    if repository.provider == Repository.Provider.GITHUB:
        if not repository.integration:
            raise ValueError(
                f"Repository {repository.full_name} has no GitHub integration. "
                "Cannot clone without installation token."
            )

        # Get installation token
        token = github_client.get_installation_token(
            installation_id=repository.integration.installation_id,
        )

        # Build authenticated clone URL
        # Format: https://x-access-token:{token}@github.com/{full_name}.git
        auth_clone_url = f"https://x-access-token:{token}@github.com/{repository.full_name}.git"

        # Ensure parent directory exists (git clone will create target_dir)
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Cloning %(repo)s (branch: %(branch)s) to %(target)s",
            {"repo": repository.full_name, "branch": branch, "target": str(target_dir)},
        )

        # Clone the repository
        result = subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                "--branch",
                branch,
                auth_clone_url,
                str(target_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            # Log error but sanitize token from output
            sanitized_stderr = result.stderr.replace(token, "***TOKEN***")
            logger.error(
                "Git clone failed: %(error)s",
                {"error": sanitized_stderr},
            )
            raise subprocess.CalledProcessError(
                returncode=result.returncode,
                cmd="git clone",
                stderr=sanitized_stderr,
            )

        logger.info(
            "Successfully cloned %(repo)s to %(target)s",
            {"repo": repository.full_name, "target": str(target_dir)},
        )
        return

    # Unsupported provider
    raise ValueError(f"Unsupported repository provider: {repository.provider}")


def cleanup_repository(repo_path: Path) -> None:
    """
    Delete a cloned repository directory.

    Only deletes paths within settings.CLAUDE_SANDBOX_DIR as a safety measure.

    Args:
        repo_path: Path to the repository directory to delete.
    """
    # Safety check: only delete if under our clone base directory
    try:
        repo_path.relative_to(settings.CLAUDE_SANDBOX_DIR)
    except ValueError:
        logger.error(
            "Refusing to delete path outside clone directory: %(path)s",
            {"path": str(repo_path)},
        )
        return

    if repo_path.exists():
        logger.info(
            "Cleaning up cloned repository: %(path)s",
            {"path": str(repo_path)},
        )
        shutil.rmtree(repo_path)
