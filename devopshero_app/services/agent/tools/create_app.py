"""
Tool for creating applications within a workspace.

This tool allows the agent to create app configurations that define
how an application should be built and deployed.
"""

from dataclasses import dataclass, asdict

from django.utils.text import slugify

from devopshero_app.models import App, Datastore, Organization, User, Workspace


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
    repo_url: str
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


def create_app(
    workspace_id: str,
    name: str,
    repo_url: str,
    branch: str,
    app_type: str,
    build_strategy: str,
    container_port: int,
    cpu: int,
    memory: int,
    health_check_path: str,
    organization: Organization,
    user: User,
    environment_variables: list[dict] | None = None,
    domain_name: str | None = None,
    datastore_id: str | None = None,
    dockerfile_path: str = "",
) -> AppSummary:
    """
    Create an application configuration in a workspace.

    Args:
        workspace_id: UUID of the workspace to create the app in.
        name: Human-readable name for the app.
        repo_url: Repository URL (file:// URLs only in v1).
        branch: Git branch to deploy from.
        app_type: Type of app (web, worker, scheduled).
        build_strategy: How to build (dockerfile, nixpacks, buildpack).
        container_port: Port the container listens on.
        cpu: Fargate CPU units (256, 512, 1024, etc.).
        memory: Fargate memory in MiB.
        health_check_path: HTTP path for health checks.
        organization: The Organization this app belongs to.
        user: The User creating the app.
        environment_variables: List of {name, value} dicts for env vars.
        domain_name: Optional custom domain.
        datastore_id: Optional UUID of datastore to bind.
        dockerfile_path: Path to Dockerfile if using dockerfile strategy.

    Returns:
        AppSummary with the created app details.

    Raises:
        ValueError: If workspace doesn't exist, doesn't belong to org,
                    or other validation fails.
    """
    # Validate workspace exists and belongs to organization
    try:
        workspace = Workspace.objects.get(
            id=workspace_id,
            organization=organization,
        )
    except Workspace.DoesNotExist:
        raise ValueError(
            f"Workspace {workspace_id} not found or doesn't belong to your organization."
        )

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
            datastore = Datastore.objects.get(
                id=datastore_id,
                workspace=workspace,
            )
        except Datastore.DoesNotExist:
            raise ValueError(
                f"Datastore {datastore_id} not found or doesn't belong to workspace."
            )

    # Generate unique slug within workspace
    base_slug = slugify(name)
    slug = base_slug
    counter = 1
    while App.objects.filter(workspace=workspace, slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Create the app
    app = App.objects.create(
        workspace=workspace,
        name=name,
        slug=slug,
        app_type=app_type,
        build_strategy=build_strategy,
        repo_url=repo_url,
        branch=branch,
        dockerfile_path=dockerfile_path,
        container_port=container_port,
        cpu=cpu,
        memory=memory,
        health_check_path=health_check_path,
        environment_variables=environment_variables or [],
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
        repo_url=app.repo_url,
        branch=app.branch,
        container_port=app.container_port,
        cpu=app.cpu,
        memory=app.memory,
        health_check_path=app.health_check_path,
        domain_name=app.domain_name or None,
        datastore_id=str(datastore.id) if datastore else None,
    )
