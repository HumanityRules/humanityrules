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


@dataclass
class SaveBlueprintResult:
    """Result of save_blueprint operation."""

    id: str
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
    environment = await Environment.objects.filter(
        aws_account__organization_id=organization_id,
        slug=environment_slug,
    ).afirst()

    if not environment:
        raise ValueError(
            f"Environment '{environment_slug}' does not exist. "
            "Use list_environments to see available environments."
        )

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
            "app", "environment",
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

        blueprint.environment_variables = _normalize_environment_variables(
            environment_variables, blueprint.environment_variables
        )
        blueprint.app_secrets = _normalize_app_secrets(app_secrets, blueprint.app_secrets)

        if datastore_id is not None:
            if datastore_id == "":
                blueprint.datastore = None
            else:
                blueprint.datastore = await Datastore.objects.aget(
                    id=datastore_id, workspace=workspace,
                )

        if blueprint.status == DeploymentBlueprint.Status.FAILED:
            blueprint.status = DeploymentBlueprint.Status.DRAFT
            blueprint.status_message = ""

        await blueprint.asave()
        created = False
    else:
        if not environment_slug:
            raise ValueError(
                "environment_slug is required when creating a new blueprint."
            )

        from devopshero_app.models import App
        app = await App.objects.select_related("repository").aget(id=conversation.context_app_id)
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

        blueprint = await DeploymentBlueprint.objects.acreate(
            app_id=conversation.context_app_id,
            environment=environment,
            status=DeploymentBlueprint.Status.DRAFT,
            branch=branch or "",
            cpu=cpu or 256,
            memory=memory or 512,
            environment_variables=_normalize_environment_variables(environment_variables, None),
            app_secrets=_normalize_app_secrets(app_secrets, None),
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
        app_name=blueprint.app.name,
        environment_name=blueprint.environment.name,
        status=blueprint.status,
        branch=blueprint.branch or "(default)",
        cpu=blueprint.cpu,
        memory=blueprint.memory,
        subdomain=blueprint.subdomain or "(default)",
        has_env_vars=bool(blueprint.environment_variables),
        has_secrets=bool(blueprint.app_secrets),
        has_datastore=blueprint.datastore_id is not None,
        created=created,
    )
