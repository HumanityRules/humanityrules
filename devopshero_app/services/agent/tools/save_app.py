"""
Tool for creating or updating an App with upsert semantics.

If the conversation has no context_app, creates a new App and sets it.
If context_app is already set, updates the existing App.
"""

from dataclasses import asdict, dataclass

from django.db import IntegrityError
from django.utils.text import slugify

from devopshero_app.models import App, Conversation, Repository, User, Workspace


@dataclass
class SaveAppResult:
    """Result of save_app operation."""

    id: str
    name: str
    slug: str
    app_type: str
    build_strategy: str
    container_port: int
    dockerfile_path: str
    health_check_path: str
    health_check_command: str
    repo_subpath: str
    repository_name: str
    created: bool

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def save_app(
    conversation: Conversation,
    workspace: Workspace,
    repository: Repository,
    user: User,
    name: str,
    app_type: str,
    build_strategy: str,
    container_port: int,
    health_check_path: str,
    dockerfile_path: str | None,
    health_check_command: str | None,
    repo_subpath: str | None,
) -> SaveAppResult:
    """Create or update an App based on conversation context."""
    organization = workspace.organization

    valid_app_types = [choice.value for choice in App.AppType]
    if app_type not in valid_app_types:
        raise ValueError(f"Invalid app_type '{app_type}'. Must be one of: {', '.join(valid_app_types)}")

    valid_strategies = [choice.value for choice in App.BuildStrategy]
    if build_strategy not in valid_strategies:
        raise ValueError(f"Invalid build_strategy '{build_strategy}'. Must be one of: {', '.join(valid_strategies)}")

    existing_app_id = conversation.context_app_id

    if existing_app_id:
        app = await App.objects.aget(id=existing_app_id)

        app.name = name
        app.app_type = app_type
        app.build_strategy = build_strategy
        app.container_port = container_port
        app.health_check_path = health_check_path
        app.dockerfile_path = dockerfile_path or ""
        app.health_check_command = health_check_command or ""
        app.repo_subpath = repo_subpath or ""
        await app.asave()
        created = False
    else:
        slug = slugify(name)
        if not slug:
            raise ValueError(f"Invalid app name '{name}': cannot generate slug.")

        try:
            app = await App.objects.acreate(
                organization=organization,
                workspace=workspace,
                repository=repository,
                name=name,
                slug=slug,
                app_type=app_type,
                build_strategy=build_strategy,
                branch=repository.default_branch,
                dockerfile_path=dockerfile_path or "",
                container_port=container_port,
                health_check_path=health_check_path,
                health_check_command=health_check_command or "",
                repo_subpath=repo_subpath or "",
                created_by=user,
            )
        except IntegrityError:
            raise ValueError(
                f"An app with slug '{slug}' already exists in this organization. "
                "Use a different name."
            )

        conversation.context_app = app
        await conversation.asave(update_fields=["context_app", "updated_at"])
        created = True

    return SaveAppResult(
        id=str(app.id),
        name=app.name,
        slug=app.slug,
        app_type=app.app_type,
        build_strategy=app.build_strategy,
        container_port=app.container_port,
        dockerfile_path=app.dockerfile_path,
        health_check_path=app.health_check_path,
        health_check_command=app.health_check_command,
        repo_subpath=app.repo_subpath,
        repository_name=repository.full_name,
        created=created,
    )
