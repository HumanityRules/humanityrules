"""
Tool for creating or updating a DeploymentBlueprint with upsert semantics.

If the conversation has no context_deployment_blueprint, creates a new blueprint and sets it.
If context_deployment_blueprint is already set, updates the existing blueprint.
"""

import json
from dataclasses import asdict, dataclass
from typing import Any

from devopshero_app.models import (
    Conversation,
    Datastore,
    DeploymentBlueprint,
    Environment,
    Workspace,
    User,
)
from devopshero_app.services import deployment_blueprint_effective_values


def _normalize_environment_variables(value: Any, existing: list | None) -> list[dict[str, str]]:
    """Normalize environment_variables input with sentinel semantics."""
    if value is None:
        return existing if existing else []

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return existing if existing else []
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return existing if existing else []

    if not isinstance(value, list):
        return existing if existing else []

    if len(value) == 0:
        return []

    result = []
    for item in value:
        if isinstance(item, dict) and "name" in item and "value" in item:
            result.append({"name": str(item["name"]), "value": str(item["value"])})

    return result


def _normalize_app_secrets(value: Any, existing: dict | None) -> dict[str, str | None] | None:
    """Normalize app_secrets input with sentinel semantics."""
    if value is None:
        return existing

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return existing
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return existing

    if not isinstance(value, dict):
        return existing

    if not value:
        return None

    result = {}
    for key, val in value.items():
        if val is None or val == "null":
            result[str(key)] = None
        else:
            result[str(key)] = str(val)

    return result if result else None


def _single_container_name(blueprint_containers: list | None, app_slug: str) -> str:
    """Return the name of the sole container in the blueprint, falling back to app_slug.

    The agent-driven chat tool treats the blueprint as single-container for now; env_vars
    + secrets updates target containers[0] by convention. Multi-container configs built
    from templates are populated via template_deploy_service.deploy_from_template instead.
    """
    if blueprint_containers:
        return blueprint_containers[0].get("name") or app_slug
    return app_slug


def _write_container_slot(
    existing: list | None,
    app_slug: str,
    env_vars: list[dict[str, str]] | None,
    app_secrets: dict[str, str | None] | None,
) -> list:
    """Produce the blueprint.containers list from the agent's flat env_vars / app_secrets inputs."""
    name = _single_container_name(existing, app_slug)
    slot: dict[str, Any] = {"name": name}
    if env_vars is not None:
        slot["environment_variables"] = env_vars
    elif existing:
        slot["environment_variables"] = existing[0].get("environment_variables", [])
    else:
        slot["environment_variables"] = []
    if app_secrets is not None:
        slot["app_secrets"] = app_secrets
    elif existing:
        slot["app_secrets"] = existing[0].get("app_secrets", {})
    else:
        slot["app_secrets"] = {}
    return [slot]


def _existing_env_vars(containers: list | None) -> list:
    if not containers:
        return []
    return list(containers[0].get("environment_variables") or [])


def _existing_app_secrets(containers: list | None) -> dict | None:
    if not containers:
        return None
    return dict(containers[0].get("app_secrets") or {}) or None


@dataclass
class SaveBlueprintResult:
    """Result of save_blueprint operation."""

    id: str
    app_id: str
    app_name: str
    environment_name: str
    status: str
    branch: str
    cpu: int
    memory: int
    subdomain: str
    has_env_vars: bool
    has_secrets: bool
    has_datastore: bool
    created: bool

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def _get_environment(organization_id: str, environment_slug: str) -> Environment:
    """Get and validate target environment."""
    environments = [
        environment async for environment in Environment.objects.filter(
            aws_account__organization_id=organization_id,
            slug=environment_slug,
        ).exclude(
            status=Environment.Status.DISCARDED,
        ).select_related("aws_account")
    ]

    if not environments:
        raise ValueError(
            f"Environment '{environment_slug}' does not exist. "
            "Use list_environments to see available environments."
        )

    if len(environments) > 1:
        raise ValueError(
            f"Multiple environments share the slug '{environment_slug}' across different AWS accounts. "
            "Choose a uniquely named environment."
        )

    environment = environments[0]

    if environment.status != Environment.Status.READY:
        raise ValueError(
            f"Environment '{environment.name}' is not ready (status: {environment.status}). "
            "Use provision_environment to ensure it's properly provisioned."
        )

    return environment


async def save_blueprint(
    conversation: Conversation,
    workspace: Workspace,
    user: User,
    environment_slug: str | None,
    branch: str | None,
    cpu: int | None,
    memory: int | None,
    environment_variables: list[dict] | None,
    app_secrets: dict | None,
    datastore_id: str | None,
    subdomain: str | None,
) -> SaveBlueprintResult:
    """Create or update a DeploymentBlueprint based on conversation context."""
    if not conversation.context_app_id:
        raise ValueError(
            "No app context set. Use save_app first to create or identify the app."
        )

    existing_blueprint_id = conversation.context_deployment_blueprint_id

    if existing_blueprint_id:
        blueprint = await DeploymentBlueprint.objects.select_related(
            "app__repository", "environment",
        ).aget(id=existing_blueprint_id)

        if blueprint.status not in (DeploymentBlueprint.Status.DRAFT, DeploymentBlueprint.Status.FAILED):
            raise ValueError(
                f"Blueprint is in '{blueprint.status}' state and cannot be edited. "
                "Only draft or failed blueprints can be updated."
            )

        if branch is not None:
            blueprint.branch = branch
        if cpu is not None:
            blueprint.cpu = cpu
        if memory is not None:
            blueprint.memory = memory
        if subdomain is not None:
            blueprint.subdomain = subdomain

        normalized_env_vars = _normalize_environment_variables(
            environment_variables, _existing_env_vars(blueprint.containers),
        )
        normalized_secrets = _normalize_app_secrets(
            app_secrets, _existing_app_secrets(blueprint.containers),
        )
        blueprint.containers = _write_container_slot(
            existing=blueprint.containers,
            app_slug=blueprint.app.slug,
            env_vars=normalized_env_vars,
            app_secrets=normalized_secrets,
        )

        if datastore_id is not None:
            if datastore_id == "":
                blueprint.datastore = None
            else:
                blueprint.datastore = await Datastore.objects.aget(
                    id=datastore_id, workspace=workspace,
                )

        if blueprint.status == DeploymentBlueprint.Status.FAILED:
            blueprint.status = DeploymentBlueprint.Status.DRAFT

        blueprint.status_message = "Ready to deploy"

        effective_values = await deployment_blueprint_effective_values.aresolve_deployment_blueprint_effective_values(
            app=blueprint.app,
            blueprint=blueprint,
        )
        await blueprint.asave()
        created = False
    else:
        if not environment_slug:
            raise ValueError(
                "environment_slug is required when creating a new blueprint."
            )

        from devopshero_app.models import App
        app = await App.objects.select_related("repository").aget(id=conversation.context_app_id)
        open_blueprint_statuses = [
            DeploymentBlueprint.Status.DRAFT,
            DeploymentBlueprint.Status.FAILED,
            DeploymentBlueprint.Status.DEPLOYING,
        ]
        open_blueprint = await DeploymentBlueprint.objects.filter(
            app_id=conversation.context_app_id,
            status__in=open_blueprint_statuses,
        ).order_by("-created_at").afirst()
        if open_blueprint:
            raise ValueError(
                "This app already has an open deployment blueprint. "
                "Resume or discard it before starting a new deployment."
            )
        environment = await _get_environment(
            organization_id=str(workspace.organization_id),
            environment_slug=environment_slug,
        )

        datastore = None
        if datastore_id:
            try:
                datastore = await Datastore.objects.aget(id=datastore_id, workspace=workspace)
            except Datastore.DoesNotExist:
                raise ValueError(f"Datastore {datastore_id} not found in workspace.")

        initial_containers = _write_container_slot(
            existing=None,
            app_slug=app.slug,
            env_vars=_normalize_environment_variables(environment_variables, None),
            app_secrets=_normalize_app_secrets(app_secrets, None),
        )

        draft_blueprint = DeploymentBlueprint(
            app=app,
            environment=environment,
            status=DeploymentBlueprint.Status.DRAFT,
            branch=branch or "",
            cpu=cpu or 256,
            memory=memory or 512,
            containers=initial_containers,
            datastore=datastore,
            subdomain=subdomain or "",
            created_by=user,
        )
        effective_values = await deployment_blueprint_effective_values.aresolve_deployment_blueprint_effective_values(
            app=app,
            blueprint=draft_blueprint,
        )

        blueprint = await DeploymentBlueprint.objects.acreate(
            app_id=conversation.context_app_id,
            environment=environment,
            status=DeploymentBlueprint.Status.DRAFT,
            status_message="Ready to deploy",
            branch=branch or "",
            cpu=cpu or 256,
            memory=memory or 512,
            containers=initial_containers,
            datastore=datastore,
            subdomain=subdomain or "",
            created_by=user,
        )

        conversation.context_deployment_blueprint = blueprint
        await conversation.asave(update_fields=["context_deployment_blueprint", "updated_at"])

        blueprint.app = app
        blueprint.environment = environment
        created = True

    return SaveBlueprintResult(
        id=str(blueprint.id),
        app_id=str(blueprint.app_id),
        app_name=blueprint.app.name,
        environment_name=blueprint.environment.name,
        status=blueprint.status,
        branch=effective_values.branch,
        cpu=blueprint.cpu,
        memory=blueprint.memory,
        subdomain=effective_values.subdomain,
        has_env_vars=bool(_existing_env_vars(blueprint.containers)),
        has_secrets=bool(_existing_app_secrets(blueprint.containers)),
        has_datastore=blueprint.datastore_id is not None,
        created=created,
    )
