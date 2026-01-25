"""
Tool for listing applications in a workspace.

This tool allows the agent to discover existing apps before creating new ones,
enabling re-deployment of existing apps instead of creating duplicates.
"""

from dataclasses import dataclass, asdict

from devopshero_app.models import App, Deployment, Workspace


@dataclass
class AppSummary:
    """Summary of an application for listing."""

    id: str
    name: str
    slug: str
    app_type: str
    branch: str
    repository_name: str
    latest_deployment_status: str | None
    latest_deployment_id: str | None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def list_apps(workspace: Workspace) -> list[AppSummary]:
    """
    List applications in a workspace.

    Use this to discover existing apps before creating new ones.
    If an app with the desired name/slug already exists, use deploy_app
    with its ID instead of calling create_app.

    Args:
        workspace: The Workspace to list apps from.

    Returns:
        List of AppSummary objects with app details and latest deployment status.
    """
    apps = []
    async for app in App.objects.filter(workspace=workspace).select_related("repository").order_by("name"):
        # Get the latest deployment for this app (if any)
        latest_deployment = await Deployment.objects.filter(app=app).order_by("-created_at").afirst()

        apps.append(
            AppSummary(
                id=str(app.id),
                name=app.name,
                slug=app.slug,
                app_type=app.app_type,
                branch=app.branch,
                repository_name=app.repository.name,
                latest_deployment_status=latest_deployment.status if latest_deployment else None,
                latest_deployment_id=str(latest_deployment.id) if latest_deployment else None,
            )
        )

    return apps
