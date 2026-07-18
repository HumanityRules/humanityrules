"""
Resolve display-ready effective values for deployment blueprints.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import QuerySet

import humanityrules_app.app_slugs as app_slugs
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


def _build_conflict_query(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
    effective_subdomain: str,
) -> QuerySet[models.Deployment]:
    """Build a queryset for an active deployment that conflicts with the effective subdomain."""
    return (
        models.Deployment.objects.filter(
            subdomain=effective_subdomain,
            environment__shared_alb_hosted_zone=blueprint.environment.shared_alb_hosted_zone,
            status__in=ACTIVE_DEPLOYMENT_STATUSES,
        )
        .exclude(app_id=app.id, environment_id=blueprint.environment.id)
        .select_related("app", "environment")
    )


def _resolve_effective_subdomain(
    blueprint: models.DeploymentBlueprint,
    effective_subdomain: str,
    conflict: models.Deployment | None,
) -> str:
    """Resolve the effective subdomain and reject hostname conflicts."""
    app_slugs.require_valid_app_hostname_label(value=effective_subdomain)
    hosted_zone = blueprint.environment.shared_alb_hosted_zone
    if not hosted_zone or conflict is None:
        return effective_subdomain

    if blueprint.subdomain:
        raise ValueError(
            f"Subdomain '{effective_subdomain}.{hosted_zone}' is already in use by "
            f"'{conflict.app.name}' in environment '{conflict.environment.name}'. "
            "Please choose a different subdomain."
        )

    raise ValueError(
        f"Subdomain '{effective_subdomain}.{hosted_zone}' is already in use. "
        "Please specify an explicit subdomain."
    )


def _build_effective_values(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
    effective_subdomain: str,
    conflict: models.Deployment | None,
) -> DeploymentBlueprintEffectiveValues:
    """Build effective blueprint values from fetched conflict data."""
    resolved_subdomain = _resolve_effective_subdomain(
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
        conflict=conflict,
    )
    hosted_zone = blueprint.environment.shared_alb_hosted_zone
    return DeploymentBlueprintEffectiveValues(
        branch=blueprint.branch or app.repository.default_branch,
        cpu_display=f"{blueprint.cpu} units ({_format_vcpu(blueprint.cpu)} vCPU)",
        subdomain=resolved_subdomain,
        url=f"https://{resolved_subdomain}.{hosted_zone}" if hosted_zone else "",
    )


def resolve_deployment_blueprint_effective_values(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
) -> DeploymentBlueprintEffectiveValues:
    """Resolve display-ready effective values for a deployment blueprint."""
    effective_subdomain = blueprint.subdomain or app.slug
    conflict = _build_conflict_query(
        app=app,
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
    ).first()
    return _build_effective_values(
        app=app,
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
        conflict=conflict,
    )


async def aresolve_deployment_blueprint_effective_values(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
) -> DeploymentBlueprintEffectiveValues:
    """Resolve effective blueprint values using async ORM queries."""
    effective_subdomain = blueprint.subdomain or app.slug
    conflict = await _build_conflict_query(
        app=app,
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
    ).afirst()
    return _build_effective_values(
        app=app,
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
        conflict=conflict,
    )
