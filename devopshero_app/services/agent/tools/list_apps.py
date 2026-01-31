"""
Tool for listing applications in a workspace.

This tool allows the agent to discover existing apps and their deployment status.
"""

from dataclasses import dataclass, asdict

from devopshero_app.models import App, Deployment, Workspace


# Statuses that represent "active" deployments (running or in-progress)
ACTIVE_DEPLOYMENT_STATUSES = [
    Deployment.Status.RUNNING,
    Deployment.Status.PENDING,
    Deployment.Status.BUILDING,
    Deployment.Status.PUSHING,
    Deployment.Status.DEPLOYING,
    Deployment.Status.STARTING,
]


@dataclass
class DeploymentInfo:
    """Summary of a deployment for an app."""

    id: str
    environment_name: str
    environment_slug: str
    subdomain: str
    hosted_zone: str
    status: str
    url: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class AppSummary:
    """Summary of an application for listing."""

    id: str
    name: str
    slug: str
    app_type: str
    branch: str
    repository_name: str
    deployments: list[DeploymentInfo]

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        result["deployments"] = [d.to_dict() for d in self.deployments]
        return result


async def list_apps(workspace: Workspace) -> list[AppSummary]:
    """
    List applications in a workspace with their active deployments.

    Returns one deployment per environment (the most recent), which is sufficient
    for the agent to understand current state and detect subdomain conflicts.

    Args:
        workspace: The Workspace to list apps from.

    Returns:
        List of AppSummary objects with app details and latest deployment per environment.
    """
    apps = []
    async for app in App.objects.filter(workspace=workspace).select_related("repository").order_by("name"):
        # Get active deployments, keep only the latest per environment
        deployments_info = []
        seen_environments: set[str] = set()

        async for deployment in Deployment.objects.filter(
            app=app,
            status__in=ACTIVE_DEPLOYMENT_STATUSES,
        ).select_related("environment").order_by("-created_at"):
            env_id = str(deployment.environment_id)
            if env_id in seen_environments:
                continue
            seen_environments.add(env_id)

            hosted_zone = deployment.environment.shared_alb_hosted_zone or ""
            subdomain = deployment.subdomain or app.slug
            url = f"https://{subdomain}.{hosted_zone}" if hosted_zone else ""

            deployments_info.append(
                DeploymentInfo(
                    id=str(deployment.id),
                    environment_name=deployment.environment.name,
                    environment_slug=deployment.environment.slug,
                    subdomain=subdomain,
                    hosted_zone=hosted_zone,
                    status=deployment.status,
                    url=url,
                )
            )

        apps.append(
            AppSummary(
                id=str(app.id),
                name=app.name,
                slug=app.slug,
                app_type=app.app_type,
                branch=app.branch,
                repository_name=app.repository.name,
                deployments=deployments_info,
            )
        )

    return apps
