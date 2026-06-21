"""
Resolve display-ready effective values for deployment blueprints.
"""

from dataclasses import dataclass
from decimal import Decimal

import humanityrules_app.models as models


ACTIVE_DEPLOYMENT_STATUSES = [
    models.Deployment.Status.SUCCEEDED,
    models.Deployment.Status.PENDING,
    models.Deployment.Status.BUILDING,
    models.Deployment.Status.PUSHING,
    models.Deployment.Status.DEPLOYING,
    models.Deployment.Status.STARTING,
]


@dataclass(frozen=True)
class DeploymentBlueprintEffectiveValues:
    """Resolved branch and routing values for a deployment blueprint."""

    branch: str
    cpu_display: str
    subdomain: str
    url: str


def _format_vcpu(cpu_units: int) -> str:
    """Format ECS CPU units as a human-readable vCPU value."""
    return format(Decimal(cpu_units) / Decimal(1024), "g")


def _build_subdomain_candidates(app: models.App, blueprint: models.DeploymentBlueprint) -> list[str]:
    """Build the possible subdomain values that may need conflict checks."""
    if blueprint.subdomain:
        return [blueprint.subdomain]
    return [app.slug, f"{app.slug}-{blueprint.environment.slug}"]


def _build_conflict_query(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
    candidates: list[str],
):
    """Build a queryset for active deployments that conflict with the candidates."""
    return (
        models.Deployment.objects.filter(
            subdomain__in=candidates,
            environment__shared_alb_hosted_zone=blueprint.environment.shared_alb_hosted_zone,
            status__in=ACTIVE_DEPLOYMENT_STATUSES,
        )
        .exclude(app_id=app.id, environment_id=blueprint.environment.id)
        .select_related("app", "environment")
    )


def _resolve_effective_subdomain(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
    conflicts_by_subdomain: dict[str, models.Deployment],
) -> str:
    """Resolve the effective subdomain, including conflict-aware auto-suffixing."""
    explicit_subdomain = blueprint.subdomain
    hosted_zone = blueprint.environment.shared_alb_hosted_zone
    if not hosted_zone:
        return explicit_subdomain or app.slug

    if explicit_subdomain:
        conflict = conflicts_by_subdomain.get(explicit_subdomain)
        if conflict:
            raise ValueError(
                f"Subdomain '{explicit_subdomain}.{hosted_zone}' is already in use by "
                f"'{conflict.app.name}' in environment '{conflict.environment.name}'. "
                "Please choose a different subdomain."
            )
        return explicit_subdomain

    default_subdomain = app.slug
    if default_subdomain not in conflicts_by_subdomain:
        return default_subdomain

    suffixed_subdomain = f"{app.slug}-{blueprint.environment.slug}"
    if suffixed_subdomain not in conflicts_by_subdomain:
        return suffixed_subdomain

    raise ValueError(
        f"Both '{default_subdomain}.{hosted_zone}' and '{suffixed_subdomain}.{hosted_zone}' are in use. "
        "Please specify an explicit subdomain."
    )


def _build_effective_values(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
    conflicts_by_subdomain: dict[str, models.Deployment],
) -> DeploymentBlueprintEffectiveValues:
    """Build effective blueprint values from fetched conflict data."""
    effective_subdomain = _resolve_effective_subdomain(
        app=app,
        blueprint=blueprint,
        conflicts_by_subdomain=conflicts_by_subdomain,
    )
    hosted_zone = blueprint.environment.shared_alb_hosted_zone
    return DeploymentBlueprintEffectiveValues(
        branch=blueprint.branch or app.repository.default_branch,
        cpu_display=f"{blueprint.cpu} units ({_format_vcpu(blueprint.cpu)} vCPU)",
        subdomain=effective_subdomain,
        url=f"https://{effective_subdomain}.{hosted_zone}" if hosted_zone else "",
    )


def resolve_deployment_blueprint_effective_values(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
) -> DeploymentBlueprintEffectiveValues:
    """Resolve display-ready effective values for a deployment blueprint."""
    candidates = _build_subdomain_candidates(app=app, blueprint=blueprint)
    conflicts = _build_conflict_query(app=app, blueprint=blueprint, candidates=candidates)
    conflicts_by_subdomain = {
        deployment.subdomain: deployment
        for deployment in conflicts
    }
    return _build_effective_values(
        app=app,
        blueprint=blueprint,
        conflicts_by_subdomain=conflicts_by_subdomain,
    )


async def aresolve_deployment_blueprint_effective_values(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
) -> DeploymentBlueprintEffectiveValues:
    """Resolve effective blueprint values using async ORM queries."""
    candidates = _build_subdomain_candidates(app=app, blueprint=blueprint)
    conflicts_by_subdomain: dict[str, models.Deployment] = {}
    async for deployment in _build_conflict_query(app=app, blueprint=blueprint, candidates=candidates):
        conflicts_by_subdomain[deployment.subdomain] = deployment
    return _build_effective_values(
        app=app,
        blueprint=blueprint,
        conflicts_by_subdomain=conflicts_by_subdomain,
    )
