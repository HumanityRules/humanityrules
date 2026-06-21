"""
Tool for repository git operations via DOH-managed credentials.

This centralizes git write operations (branching, commit, push, PR creation)
behind GitHub App installation auth instead of local machine credentials.
"""

import asyncio
import logging
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from humanityrules_app.models import Repository
from humanityrules_app.services.agent.sandbox import get_sandbox_paths
from humanityrules_app.services.gitproviders import github_client


logger = logging.getLogger(__name__)


@dataclass
class GitOperationResult:
    """Result of a git operation executed in the sandbox repository."""

    action: str
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def _sanitize_git_output(text: str, token: str | None) -> str:
    """Remove credential material from git outputs."""
    if not text:
        return text

    sanitized = text
    if token:
        sanitized = sanitized.replace(token, "***TOKEN***")
    sanitized = re.sub(r"x-access-token:[^@]+@", "x-access-token:***TOKEN***@", sanitized)
    return sanitized


def _normalize_multiline_text(text: str) -> str:
    """Normalize text that may contain escaped newline sequences."""
    normalized = text.replace("\r\n", "\n")
    if "\\n" in normalized and "\n" not in normalized:
        normalized = normalized.replace("\\n", "\n")
    return normalized


def _validate_files(files: list[str]) -> None:
    """Validate file paths are repository-relative and safe."""
    for file_path in files:
        file_obj = Path(file_path)
        if file_obj.is_absolute():
            raise ValueError(f"Absolute paths are not allowed: {file_path}")
        if ".." in file_obj.parts:
            raise ValueError(f"Parent directory references are not allowed: {file_path}")


def _get_current_branch(repo_path: Path) -> str:
    """Return the currently checked-out branch."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"Failed to detect current branch: {result.stderr.strip()}")
    return result.stdout.strip()


def _run_git_command(repo_path: Path, command: list[str], token: str | None) -> GitOperationResult:
    """Run a git command and return normalized result."""
    result = subprocess.run(
        command,
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return GitOperationResult(
        action=" ".join(command[:3]),
        success=result.returncode == 0,
        stdout=_sanitize_git_output(result.stdout, token=token),
        stderr=_sanitize_git_output(result.stderr, token=token),
        exit_code=result.returncode,
        data={},
    )


def _build_authenticated_remote(repository: Repository, token: str) -> str:
    """Build an authenticated HTTPS remote URL for one-off git network calls."""
    return f"https://x-access-token:{token}@github.com/{repository.full_name}.git"


def _create_pull_request(repository: Repository, token: str, title: str, body: str, head_branch: str, base_branch: str) -> dict[str, Any]:
    """Create a GitHub pull request using GitHub App installation token."""
    response = httpx.post(
        url=f"https://api.github.com/repos/{repository.full_name}/pulls",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise ValueError(f"GitHub PR creation failed ({response.status_code}): {response.text}")

    data = response.json()
    return _extract_pull_request_data(data=data)


def _extract_pull_request_data(data: dict[str, Any]) -> dict[str, Any]:
    """Extract normalized pull request fields from GitHub API response."""
    return {
        "pull_request_url": data.get("html_url"),
        "pull_request_number": data.get("number"),
        "pull_request_state": data.get("state"),
        "pull_request_title": data.get("title"),
        "pull_request_is_draft": data.get("draft"),
        "pull_request_merged": data.get("merged"),
        "pull_request_merged_at": data.get("merged_at"),
    }


def _require_github_token(repository: Repository) -> str:
    """Get a fresh GitHub installation token for the repository integration."""
    if repository.provider != Repository.Provider.GITHUB:
        raise ValueError(f"Repository provider '{repository.provider}' is not supported for this action.")
    if not repository.integration:
        raise ValueError("Repository has no GitHub integration. Cannot authenticate git operation.")
    return github_client.get_installation_token(installation_id=repository.integration.installation_id)


def _parse_pull_number(pull_number: Any) -> int:
    """Parse and validate pull request number input."""
    if isinstance(pull_number, int):
        parsed = pull_number
    elif isinstance(pull_number, str) and pull_number.isdigit():
        parsed = int(pull_number)
    else:
        raise ValueError("pull_number must be a positive integer")

    if parsed <= 0:
        raise ValueError("pull_number must be a positive integer")
    return parsed


def _get_pull_request(repository: Repository, token: str, pull_number: int) -> dict[str, Any]:
    """Fetch pull request details from GitHub."""
    response = httpx.get(
        url=f"https://api.github.com/repos/{repository.full_name}/pulls/{pull_number}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise ValueError(f"GitHub PR fetch failed ({response.status_code}): {response.text}")
    data = response.json()
    return _extract_pull_request_data(data=data)


def _update_pull_request(repository: Repository, token: str, pull_number: int, body: str, title: str | None) -> dict[str, Any]:
    """Update pull request title/body on GitHub."""
    payload: dict[str, Any] = {"body": body}
    if isinstance(title, str) and title.strip():
        payload["title"] = title

    response = httpx.patch(
        url=f"https://api.github.com/repos/{repository.full_name}/pulls/{pull_number}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=payload,
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise ValueError(f"GitHub PR update failed ({response.status_code}): {response.text}")
    data = response.json()
    return _extract_pull_request_data(data=data)


def _execute_action(repo_path: Path, repository: Repository, action: str, parameters: dict[str, Any], token: str | None) -> GitOperationResult:
    """Execute a single git action inside the sandbox repository."""
    if action == "status":
        command = ["git", "status", "--short", "--branch"]
        result = _run_git_command(repo_path=repo_path, command=command, token=token)
        result.action = action
        return result

    if action == "diff":
        include_staged = bool(parameters.get("include_staged"))
        command = ["git", "diff", "--cached"] if include_staged else ["git", "diff"]
        result = _run_git_command(repo_path=repo_path, command=command, token=token)
        result.action = action
        return result

    if action == "log":
        raw_limit = parameters.get("limit")
        limit = int(raw_limit) if raw_limit else 5
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        command = ["git", "log", "--oneline", f"-{limit}"]
        result = _run_git_command(repo_path=repo_path, command=command, token=token)
        result.action = action
        result.data = {"limit": limit}
        return result

    if action == "create_branch":
        branch_name = parameters.get("branch_name")
        if not isinstance(branch_name, str) or not branch_name.strip():
            raise ValueError("branch_name is required for create_branch action")
        command = ["git", "checkout", "-b", branch_name]
        result = _run_git_command(repo_path=repo_path, command=command, token=token)
        result.action = action
        result.data = {"branch_name": branch_name}
        return result

    if action == "stage_files":
        files = parameters.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("files is required for stage_files action")
        _validate_files(files=files)
        command = ["git", "add", "--", *files]
        result = _run_git_command(repo_path=repo_path, command=command, token=token)
        result.action = action
        result.data = {"files": files}
        return result

    if action == "commit":
        commit_message = parameters.get("commit_message")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise ValueError("commit_message is required for commit action")
        normalized_commit_message = _normalize_multiline_text(text=commit_message)
        command = [
            "git",
            "-c", "user.name=DevOps Hero",
            "-c", "user.email=bot@humanityrules.io",
            "commit", "-m", normalized_commit_message,
        ]
        result = _run_git_command(repo_path=repo_path, command=command, token=token)
        result.action = action
        return result

    if action == "push_branch":
        branch_name = parameters.get("branch_name")
        if not isinstance(branch_name, str) or not branch_name.strip():
            raise ValueError("branch_name is required for push_branch action")

        if token and repository.provider == Repository.Provider.GITHUB:
            remote = _build_authenticated_remote(repository=repository, token=token)
            command = ["git", "push", "-u", remote, branch_name]
        else:
            command = ["git", "push", "-u", "origin", branch_name]
        result = _run_git_command(repo_path=repo_path, command=command, token=token)
        result.action = action
        result.data = {"branch_name": branch_name}
        return result

    if action == "create_pull_request":
        if repository.provider != Repository.Provider.GITHUB:
            raise ValueError("create_pull_request is only supported for GitHub repositories")
        if not token:
            raise ValueError("No GitHub token available for pull request creation")

        title = parameters.get("title")
        body = parameters.get("body")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title is required for create_pull_request action")
        if not isinstance(body, str):
            raise ValueError("body is required for create_pull_request action")
        normalized_body = _normalize_multiline_text(text=body)

        head_branch = parameters.get("head_branch") or _get_current_branch(repo_path=repo_path)
        base_branch = parameters.get("base_branch") or repository.default_branch

        data = _create_pull_request(
            repository=repository,
            token=token,
            title=title,
            body=normalized_body,
            head_branch=head_branch,
            base_branch=base_branch,
        )
        return GitOperationResult(
            action=action,
            success=True,
            stdout="Pull request created successfully.",
            stderr="",
            exit_code=0,
            data=data,
        )

    if action == "update_pull_request":
        if repository.provider != Repository.Provider.GITHUB:
            raise ValueError("update_pull_request is only supported for GitHub repositories")
        if not token:
            raise ValueError("No GitHub token available for pull request update")

        pull_number = _parse_pull_number(pull_number=parameters.get("pull_number"))
        body = parameters.get("body")
        title = parameters.get("title")
        if not isinstance(body, str):
            raise ValueError("body is required for update_pull_request action")
        normalized_body = _normalize_multiline_text(text=body)

        data = _update_pull_request(
            repository=repository,
            token=token,
            pull_number=pull_number,
            body=normalized_body,
            title=title,
        )
        return GitOperationResult(
            action=action,
            success=True,
            stdout="Pull request updated successfully.",
            stderr="",
            exit_code=0,
            data=data,
        )

    if action == "get_pull_request":
        if repository.provider != Repository.Provider.GITHUB:
            raise ValueError("get_pull_request is only supported for GitHub repositories")
        if not token:
            raise ValueError("No GitHub token available for pull request lookup")

        pull_number = _parse_pull_number(pull_number=parameters.get("pull_number"))
        data = _get_pull_request(
            repository=repository,
            token=token,
            pull_number=pull_number,
        )
        return GitOperationResult(
            action=action,
            success=True,
            stdout="Pull request fetched successfully.",
            stderr="",
            exit_code=0,
            data=data,
        )

    if action == "get_remote_info":
        command = ["git", "remote", "-v"]
        result = _run_git_command(repo_path=repo_path, command=command, token=token)
        result.action = action
        return result

    raise ValueError(f"Unsupported git action: {action}")


async def run_git_operation(conversation_id, repository: Repository, action: str, parameters: dict[str, Any]) -> GitOperationResult:
    """Run one git action in the conversation sandbox using DOH-managed auth."""
    sandbox_paths = get_sandbox_paths(conversation_id=conversation_id)
    repo_path = sandbox_paths.src_path

    if not repo_path.exists():
        raise ValueError(f"Repository sandbox does not exist: {repo_path}")
    if not (repo_path / ".git").exists():
        raise ValueError(f"Sandbox path is not a git repository: {repo_path}")

    token = None
    if action in {"push_branch", "create_pull_request", "update_pull_request", "get_pull_request"}:
        token = await asyncio.to_thread(_require_github_token, repository)

    try:
        return await asyncio.to_thread(
            _execute_action,
            repo_path,
            repository,
            action,
            parameters,
            token,
        )
    except Exception as exc:
        logger.error("git_ops action failed: %(action)s: %(error)s", {"action": action, "error": str(exc)})
        sanitized_error = _sanitize_git_output(str(exc), token=token)
        return GitOperationResult(
            action=action,
            success=False,
            stdout="",
            stderr=sanitized_error,
            exit_code=1,
            data={},
        )
