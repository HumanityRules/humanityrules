"""
Tool for creating applications within a workspace.

This tool allows the agent to create app configurations that define
how an application should be built and deployed.
"""

import json
from dataclasses import dataclass, asdict
from typing import Any

from django.utils.text import slugify

from devopshero_app.models import App, Datastore, Repository, User, Workspace


def _normalize_environment_variables(value: Any) -> list[dict[str, str]]:
    """
    Normalize environment_variables input to the expected list format.

    Handles common LLM mistakes like sending strings instead of objects.
    Expected format: [{"name": "FOO", "value": "bar"}, ...]

    Returns empty list for invalid/empty inputs.
    """
    # Handle None or empty
    if value is None:
        return []

    # Handle string input (LLM might send "{}" or "[]" as string)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []

    # Must be a list at this point
    if not isinstance(value, list):
        return []

    # Validate each item is a dict with name/value keys
    result = []
    for item in value:
        if isinstance(item, dict) and "name" in item and "value" in item:
            result.append({"name": str(item["name"]), "value": str(item["value"])})

    return result


def _normalize_app_secrets(value: Any) -> dict[str, str | None] | None:
    """
    Normalize app_secrets input from LLM.

    Expected format: {"field_name": "value" or null, ...}
    - None/empty -> None
    - Empty dict -> None
    - String "null" values -> Python None (auto-generate)
    """
    # Handle None or empty
    if value is None:
        return None

    # Handle string input (LLM might send "{}" as string)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None

    # Must be a dict at this point
    if not isinstance(value, dict):
        return None

    # Empty dict -> None
    if not value:
        return None

    # Normalize values: string "null" -> Python None
    result = {}
    for key, val in value.items():
        if val is None or val == "null":
            result[str(key)] = None
        else:
            result[str(key)] = str(val)

    return result if result else None


@dataclass
class AppSummary:
    """Summary of a created application."""

    id: str
    name: str
    slug: str
    workspace_id: str
    workspace_name: str
    app_type: str
    build_strategy: str
    branch: str
    container_port: int
    cpu: int
    memory: int
    health_check_path: str
    datastore_id: str | None
    app_secrets: dict[str, str | None] | None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def create_app(
    workspace: Workspace,
    repository: Repository,
    name: str,
    branch: str,
    app_type: str,
    build_strategy: str,
    container_port: int,
    cpu: int,
    memory: int,
    health_check_path: str,
    user: User,
    environment_variables: list[dict] | None,
    datastore_id: str | None,
    dockerfile_path: str,
    app_secrets: dict | None,
) -> AppSummary:
    """
    Create an application configuration in a workspace.

    Args:
        workspace: The Workspace to create the app in (from conversation context).
        repository: The Repository containing the app source code.
        name: Human-readable name for the app.
        branch: Git branch to deploy from.
        app_type: Type of app (web, worker, scheduled).
        build_strategy: How to build (dockerfile, nixpacks, buildpack).
        container_port: Port the container listens on.
        cpu: Fargate CPU units (256, 512, 1024, etc.).
        memory: Fargate memory in MiB.
        health_check_path: HTTP path for health checks.
        user: The User creating the app.
        environment_variables: List of {name, value} dicts for env vars.
        datastore_id: UUID of datastore to bind.
        dockerfile_path: Path to Dockerfile if using dockerfile strategy.
        app_secrets: Dict mapping secret field names to values. Use null to auto-generate.

    Returns:
        AppSummary with the created app details.

    Raises:
        ValueError: If validation fails.
    """
    # Validate app_type
    valid_app_types = [choice.value for choice in App.AppType]
    if app_type not in valid_app_types:
        raise ValueError(f"Invalid app_type '{app_type}'. Must be one of: {', '.join(valid_app_types)}")

    # Validate build_strategy
    valid_strategies = [choice.value for choice in App.BuildStrategy]
    if build_strategy not in valid_strategies:
        raise ValueError(f"Invalid build_strategy '{build_strategy}'. Must be one of: {', '.join(valid_strategies)}")

    # Validate datastore if provided
    datastore = None
    if datastore_id:
        try:
            datastore = await Datastore.objects.aget(
                id=datastore_id,
                workspace=workspace,
            )
        except Datastore.DoesNotExist:
            raise ValueError(
                f"Datastore {datastore_id} not found or doesn't belong to workspace."
            )

    # Generate slug unique within organization
    organization = workspace.organization
    base_slug = slugify(name)
    slug = base_slug
    counter = 1
    while await App.objects.filter(organization=organization, slug=slug).aexists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Normalize app_secrets
    normalized_app_secrets = _normalize_app_secrets(app_secrets)

    # Create the app
    app = await App.objects.acreate(
        organization=organization,
        workspace=workspace,
        repository=repository,
        name=name,
        slug=slug,
        app_type=app_type,
        build_strategy=build_strategy,
        branch=branch,
        dockerfile_path=dockerfile_path or "",
        container_port=container_port,
        cpu=cpu,
        memory=memory,
        health_check_path=health_check_path,
        environment_variables=_normalize_environment_variables(environment_variables),
        datastore=datastore,
        app_secrets=normalized_app_secrets,
        created_by=user,
    )

    return AppSummary(
        id=str(app.id),
        name=app.name,
        slug=app.slug,
        workspace_id=str(workspace.id),
        workspace_name=workspace.name,
        app_type=app.app_type,
        build_strategy=app.build_strategy,
        branch=app.branch,
        container_port=app.container_port,
        cpu=app.cpu,
        memory=app.memory,
        health_check_path=app.health_check_path,
        datastore_id=str(datastore.id) if datastore else None,
        app_secrets=app.app_secrets,
    )
