"""
Tool for creating applications within a workspace.

This tool allows the agent to create app configurations that define
how an application should be built and deployed.
"""

import json
from dataclasses import dataclass, asdict
from typing import Any

from django.utils.text import slugify

from devopshero_app.models import App, Datastore, User, Workspace


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
    domain_name: str | None
    datastore_id: str | None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def create_app(
    workspace: Workspace,
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
    domain_name: str | None,
    datastore_id: str | None,
    dockerfile_path: str,
) -> AppSummary:
    """
    Create an application configuration in a workspace.

    The repository URL is inherited from workspace.primary_repo_url.

    Args:
        workspace: The Workspace to create the app in (from conversation context).
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
        domain_name: Custom domain.
        datastore_id: UUID of datastore to bind.
        dockerfile_path: Path to Dockerfile if using dockerfile strategy.

    Returns:
        AppSummary with the created app details.

    Raises:
        ValueError: If validation fails.
    """
    # Validate app_type
    valid_app_types = [choice[0] for choice in App.AppType.choices]
    if app_type not in valid_app_types:
        raise ValueError(
            f"Invalid app_type '{app_type}'. Must be one of: {', '.join(valid_app_types)}"
        )

    # Validate build_strategy
    valid_strategies = [choice[0] for choice in App.BuildStrategy.choices]
    if build_strategy not in valid_strategies:
        raise ValueError(
            f"Invalid build_strategy '{build_strategy}'. Must be one of: {', '.join(valid_strategies)}"
        )

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

    # Generate globally unique slug (app names must be unique across all workspaces)
    base_slug = slugify(name)
    slug = base_slug
    counter = 1
    while await App.objects.filter(slug=slug).aexists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Create the app
    app = await App.objects.acreate(
        workspace=workspace,
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
        domain_name=domain_name or "",
        datastore=datastore,
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
        domain_name=app.domain_name or None,
        datastore_id=str(datastore.id) if datastore else None,
    )
