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


def _active_label_query(hosted_zone: str, subdomain: str) -> QuerySet[models.Deployment]:
    """Active deployments serving this hostname label on the hosted zone."""
    return models.Deployment.objects.filter(
        subdomain=subdomain,
        environment__shared_alb_hosted_zone=hosted_zone,
        status__in=ACTIVE_DEPLOYMENT_STATUSES,
    )


def _build_conflict_query(app: models.App, blueprint: models.DeploymentBlueprint, effective_subdomain: str) -> QuerySet[models.Deployment]:
    """Build a queryset for an active deployment that conflicts with the effective subdomain."""
    return _active_label_query(
        hosted_zone=blueprint.environment.shared_alb_hosted_zone,
        subdomain=effective_subdomain,
    ).exclude(app_id=app.id, environment_id=blueprint.environment.id)


async def araise_for_new_app_subdomain_conflict(environment: models.Environment, subdomain: str) -> None:
    """Reject creating an app whose default hostname label an active deployment already serves."""
    hosted_zone = environment.shared_alb_hosted_zone
    if not hosted_zone:
        return
    label_taken = await _active_label_query(hosted_zone=hosted_zone, subdomain=subdomain).aexists()
    if label_taken:
        raise ValueError(f"'{subdomain}.{hosted_zone}' is already in use. Please choose a different name.")


def _resolve_effective_subdomain(blueprint: models.DeploymentBlueprint, effective_subdomain: str, label_taken: bool) -> str:
    """Resolve the effective subdomain and reject hostname conflicts.

    Conflicts can come from any organization (labels are global on the shared hosted
    zone), so the message never names the conflicting app or environment.
    """
    app_slugs.require_valid_app_hostname_label(value=effective_subdomain)
    hosted_zone = blueprint.environment.shared_alb_hosted_zone
    if not hosted_zone or not label_taken:
        return effective_subdomain

    if blueprint.subdomain:
        raise ValueError(
            f"Subdomain '{effective_subdomain}.{hosted_zone}' is already in use. "
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
    label_taken: bool,
) -> DeploymentBlueprintEffectiveValues:
    """Build effective blueprint values from fetched conflict data."""
    resolved_subdomain = _resolve_effective_subdomain(
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
        label_taken=label_taken,
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
    label_taken = _build_conflict_query(
        app=app,
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
    ).exists()
    return _build_effective_values(
        app=app,
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
        label_taken=label_taken,
    )


async def aresolve_deployment_blueprint_effective_values(
    app: models.App,
    blueprint: models.DeploymentBlueprint,
) -> DeploymentBlueprintEffectiveValues:
    """Resolve effective blueprint values using async ORM queries."""
    effective_subdomain = blueprint.subdomain or app.slug
    label_taken = await _build_conflict_query(
        app=app,
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
    ).aexists()
    return _build_effective_values(
        app=app,
        blueprint=blueprint,
        effective_subdomain=effective_subdomain,
        label_taken=label_taken,
    )
