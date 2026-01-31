"""
Tool for deploying applications with upsert semantics.

Creates or updates an App record, then creates a Deployment with PENDING status.
The job worker picks up pending deployments and executes them via CDK infrastructure.

This is the unified deployment tool - it handles both new apps and redeployments.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from django.db import IntegrityError
from django.utils.text import slugify

from devopshero_app.models import (
    App,
    Datastore,
    Deployment,
    DeploymentLog,
    Environment,
    Organization,
    Repository,
    User,
    Workspace,
)


# =============================================================================
# Normalization Helpers
# =============================================================================


def _normalize_environment_variables(value: Any, existing: list | None) -> list[dict[str, str]]:
    """
    Normalize environment_variables input with sentinel semantics.

    Sentinel values:
    - None (not provided) -> keep existing unchanged
    - [] (empty list) -> clear all
    - [...] (with values) -> replace with provided

    Handles common LLM mistakes like sending strings instead of objects.
    Expected format: [{"name": "FOO", "value": "bar"}, ...]
    """
    # None = keep existing unchanged
    if value is None:
        return existing if existing else []

    # Handle string input (LLM might send "{}" or "[]" as string)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return existing if existing else []
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return existing if existing else []

    # Must be a list at this point
    if not isinstance(value, list):
        return existing if existing else []

    # Empty list = clear all (sentinel)
    if len(value) == 0:
        return []

    # Validate each item is a dict with name/value keys
    result = []
    for item in value:
        if isinstance(item, dict) and "name" in item and "value" in item:
            result.append({"name": str(item["name"]), "value": str(item["value"])})

    return result


def _normalize_app_secrets(value: Any, existing: dict | None) -> dict[str, str | None] | None:
    """
    Normalize app_secrets input with sentinel semantics.

    Sentinel values:
    - None (not provided) -> keep existing unchanged
    - {} (empty dict) -> clear all (return None)
    - {...} (with values) -> replace with provided

    Expected format: {"field_name": "value" or null, ...}
    String "null" values -> Python None (auto-generate)
    """
    # None = keep existing unchanged
    if value is None:
        return existing

    # Handle string input (LLM might send "{}" as string)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return existing
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return existing

    # Must be a dict at this point
    if not isinstance(value, dict):
        return existing

    # Empty dict = clear all (sentinel)
    if not value:
        return None

    # Normalize values: string "null" -> Python None
    result = {}
    for key, val in value.items():
        if val is None or val == "null":
            result[str(key)] = None
        else:
            result[str(key)] = str(val)

    return result if result else None


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class DeploymentSummary:
    """Summary of a created deployment with app info."""

    id: str
    app_id: str
    app_name: str
    app_slug: str
    app_created: bool  # True if app was created, False if updated
    environment_name: str
    subdomain: str
    url: str  # Full URL (e.g., https://my-app.example.com)
    git_ref: str
    status: str
    status_message: str
    image_tag: str
    created_at: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


# =============================================================================
# Helper Functions
# =============================================================================


def _generate_image_tag(app: App, git_ref: str) -> str:
    """Generate a unique image tag for this deployment."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_ref = git_ref[:8] if len(git_ref) > 8 else git_ref
    return f"{app.slug}-{short_ref}-{timestamp}"


async def _create_initial_logs(deployment: Deployment) -> None:
    """Create initial deployment log entries."""
    params = {
        "app_name": deployment.app.name,
        "git_ref": deployment.git_ref,
        "image_tag": deployment.image_tag,
        "environment_name": deployment.environment.name,
    }
    template = "Deployment queued for %(app_name)s in %(environment_name)s (git_ref=%(git_ref)s, image_tag=%(image_tag)s)"
    await DeploymentLog.objects.acreate(
        deployment=deployment,
        level=DeploymentLog.Level.INFO,
        message=template % params,
        details={
            "template": template,
            "params": params,
        },
    )


async def _validate_datastore(datastore_id: str | None, workspace: Workspace) -> Datastore | None:
    """Validate and fetch datastore if provided."""
    if not datastore_id:
        return None
    try:
        return await Datastore.objects.aget(id=datastore_id, workspace=workspace)
    except Datastore.DoesNotExist:
        raise ValueError(f"Datastore {datastore_id} not found or doesn't belong to workspace.")


async def _get_environment(organization: Organization, environment_slug: str) -> Environment:
    """Get and validate target environment."""
    environment = await Environment.objects.filter(
        aws_account__organization=organization,
        slug=environment_slug,
    ).afirst()

    if not environment:
        raise ValueError(
            f"Environment '{environment_slug}' does not exist for this AWS account. "
            "Use list_environments to see available environments, or create_environment to create one first."
        )

    # Verify environment is READY (provisioned)
    if environment.status != Environment.Status.READY:
        if environment.status == Environment.Status.PENDING:
            raise ValueError(
                f"Environment '{environment.name}' exists but has not been provisioned yet. "
                "Use create_environment to provision it before deploying."
            )
        if environment.status == Environment.Status.PROVISIONING:
            raise ValueError(
                f"Environment '{environment.name}' is currently being provisioned. "
                "Please wait for it to complete before deploying."
            )
        if environment.status == Environment.Status.ERROR:
            raise ValueError(
                f"Environment '{environment.name}' failed to provision. "
                f"Error: {environment.status_message}. "
                "Please resolve the issue or create a new environment."
            )
        raise ValueError(
            f"Environment '{environment.name}' is not ready (status: {environment.status}). "
            "Use create_environment to ensure it's properly provisioned."
        )

    return environment


async def _check_active_deployments(app: App) -> None:
    """Check for existing active deployments and raise if found."""
    active_statuses = [
        Deployment.Status.PENDING,
        Deployment.Status.BUILDING,
        Deployment.Status.PUSHING,
        Deployment.Status.DEPLOYING,
        Deployment.Status.STARTING,
    ]
    active_deployment = await Deployment.objects.filter(
        app=app,
        status__in=active_statuses,
    ).afirst()

    if active_deployment:
        raise ValueError(
            f"App '{app.name}' already has an active deployment in progress "
            f"(status: {active_deployment.status}). Please wait for it to complete."
        )


async def _check_subdomain_conflict(subdomain: str, hosted_zone: str, exclude_deployment_key: tuple[str, str] | None) -> Deployment | None:
    """
    Check if a subdomain is already in use by a running deployment in the same hosted zone.

    Args:
        subdomain: The subdomain to check.
        hosted_zone: The hosted zone (domain) to check within.
        exclude_deployment_key: Optional (app_id, environment_id) tuple to exclude from check.
            Used when redeploying to the same environment - we're replacing that deployment.
    """
    running_statuses = [
        Deployment.Status.RUNNING,
        Deployment.Status.PENDING,
        Deployment.Status.BUILDING,
        Deployment.Status.PUSHING,
        Deployment.Status.DEPLOYING,
        Deployment.Status.STARTING,
    ]
    query = Deployment.objects.filter(
        subdomain=subdomain,
        environment__shared_alb_hosted_zone=hosted_zone,
        status__in=running_statuses,
    )
    if exclude_deployment_key:
        # Exclude only the specific app+environment combo (we're replacing that deployment)
        app_id, environment_id = exclude_deployment_key
        query = query.exclude(app_id=app_id, environment_id=environment_id)
    return await query.select_related("app", "environment").afirst()


async def _resolve_subdomain(app_slug: str, environment: Environment, explicit_subdomain: str | None, app_id: str | None) -> str:
    """
    Resolve the effective subdomain for a deployment.

    If explicit_subdomain is provided, validates it doesn't conflict.
    Otherwise, defaults to app_slug, auto-suffixing with -{env_slug} if conflict.
    """
    hosted_zone = environment.shared_alb_hosted_zone
    if not hosted_zone:
        # No domain configured - subdomain doesn't matter for Route53, but store it anyway
        return explicit_subdomain or app_slug

    # When redeploying to the same environment, exclude that specific deployment from conflict check
    # (we're replacing it, so it's not a conflict)
    exclude_key = (app_id, str(environment.id)) if app_id else None

    if explicit_subdomain:
        # User provided explicit subdomain - check for conflict
        conflict = await _check_subdomain_conflict(
            subdomain=explicit_subdomain,
            hosted_zone=hosted_zone,
            exclude_deployment_key=exclude_key,
        )
        if conflict:
            raise ValueError(
                f"Subdomain '{explicit_subdomain}.{hosted_zone}' is already in use by "
                f"'{conflict.app.name}' in environment '{conflict.environment.name}'. "
                "Please choose a different subdomain."
            )
        return explicit_subdomain

    # Default to app_slug
    subdomain = app_slug

    # Check for conflict
    conflict = await _check_subdomain_conflict(
        subdomain=subdomain,
        hosted_zone=hosted_zone,
        exclude_deployment_key=exclude_key,
    )

    if conflict:
        # Auto-suffix with environment slug
        subdomain = f"{app_slug}-{environment.slug}"

        # Check if suffixed version also conflicts (unlikely but possible)
        conflict = await _check_subdomain_conflict(
            subdomain=subdomain,
            hosted_zone=hosted_zone,
            exclude_deployment_key=exclude_key,
        )
        if conflict:
            raise ValueError(
                f"Both '{app_slug}.{hosted_zone}' and '{subdomain}.{hosted_zone}' are in use. "
                "Please specify an explicit subdomain using the 'subdomain' parameter."
            )

    return subdomain


# =============================================================================
# Main Tool Function
# =============================================================================


async def deploy_app(
    workspace: Workspace,
    repository: Repository,
    name: str,
    branch: str,
    app_type: str,
    build_strategy: str,
    container_port: int,
    cpu: int,
    memory: int,
    health_check_path: str,
    user: User,
    environment_slug: str,
    git_ref: str,
    environment_variables: list[dict] | None,
    datastore_id: str | None,
    dockerfile_path: str | None,
    app_secrets: dict | None,
    subdomain: str | None,
) -> DeploymentSummary:
    """
    Deploy an application with upsert semantics.

    Creates the app if it doesn't exist, updates config if it does, then deploys.

    Matching: App is identified by (organization, slug) where slug = slugify(name).

    Subdomain resolution:
    - If subdomain is provided, validates it doesn't conflict with running deployments.
    - If not provided, defaults to app slug.
    - If default conflicts (same domain already in use), auto-suffixes with -{env_slug}.

    Sentinel semantics for environment_variables and app_secrets:
    - None (not provided) -> keep existing unchanged
    - [] or {} (empty) -> clear all
    - [...] or {...} (with values) -> replace with provided

    Args:
        workspace: The Workspace to deploy in.
        repository: The Repository containing the app source code.
        name: Human-readable name for the app (used to derive slug for matching).
        branch: Git branch to deploy from.
        app_type: Type of app (web, worker, scheduled).
        build_strategy: How to build (dockerfile, nixpacks, buildpack).
        container_port: Port the container listens on.
        cpu: Fargate CPU units (256, 512, 1024, etc.).
        memory: Fargate memory in MiB.
        health_check_path: HTTP path for health checks.
        user: The User initiating the deployment.
        environment_slug: Target environment slug (e.g., "default").
        git_ref: Git reference (branch, tag, or commit SHA) to deploy.
        environment_variables: List of {name, value} dicts. None=keep, []=clear, [values]=replace.
        datastore_id: UUID of datastore to bind. None to keep existing.
        dockerfile_path: Path to Dockerfile if using dockerfile strategy.
        app_secrets: Dict mapping secret field names to values. None=keep, {}=clear, {values}=replace.
        subdomain: Route53 subdomain override. None = use app slug (auto-suffixed if conflict).

    Returns:
        DeploymentSummary with deployment details and app_created flag.

    Raises:
        ValueError: If validation fails or repository mismatch.
    """
    organization = workspace.organization

    # Validate app_type
    valid_app_types = [choice.value for choice in App.AppType]
    if app_type not in valid_app_types:
        raise ValueError(f"Invalid app_type '{app_type}'. Must be one of: {', '.join(valid_app_types)}")

    # Validate build_strategy
    valid_strategies = [choice.value for choice in App.BuildStrategy]
    if build_strategy not in valid_strategies:
        raise ValueError(f"Invalid build_strategy '{build_strategy}'. Must be one of: {', '.join(valid_strategies)}")

    # Validate datastore if provided
    datastore = await _validate_datastore(datastore_id, workspace)

    # Validate environment
    environment = await _get_environment(organization, environment_slug)

    # Generate slug for matching
    slug = slugify(name)
    if not slug:
        raise ValueError(f"Invalid app name '{name}': cannot generate slug.")

    # Look up existing app by (organization, slug)
    existing_app = await App.objects.filter(organization=organization, slug=slug).afirst()
    app_created = False

    if existing_app:
        # Guard: repository mismatch is a hard error
        if existing_app.repository_id != repository.id:
            existing_repo = await Repository.objects.aget(id=existing_app.repository_id)
            raise ValueError(
                f"App '{name}' (slug: {slug}) already exists but is linked to a different repository "
                f"('{existing_repo.full_name}'). Cannot redeploy with repository '{repository.full_name}'. "
                "Use a different app name or update the existing app's repository manually."
            )

        # Update config fields (sentinel semantics for env/secrets)
        existing_app.name = name
        existing_app.workspace = workspace
        existing_app.branch = branch
        existing_app.app_type = app_type
        existing_app.build_strategy = build_strategy
        existing_app.container_port = container_port
        existing_app.cpu = cpu
        existing_app.memory = memory
        existing_app.health_check_path = health_check_path
        existing_app.dockerfile_path = dockerfile_path or ""

        # Sentinel semantics: None=keep, []=clear, [values]=replace
        existing_app.environment_variables = _normalize_environment_variables(
            environment_variables, existing_app.environment_variables
        )
        existing_app.app_secrets = _normalize_app_secrets(app_secrets, existing_app.app_secrets)

        # Update datastore only if explicitly provided
        if datastore_id is not None:
            existing_app.datastore = datastore

        await existing_app.asave()
        app = existing_app
    else:
        # Create new app
        try:
            app = await App.objects.acreate(
                organization=organization,
                workspace=workspace,
                repository=repository,
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
                environment_variables=_normalize_environment_variables(environment_variables, None),
                datastore=datastore,
                app_secrets=_normalize_app_secrets(app_secrets, None),
                created_by=user,
            )
            app_created = True
        except IntegrityError:
            # Race condition: another request created the app. Re-fetch and update.
            existing_app = await App.objects.filter(organization=organization, slug=slug).afirst()
            if existing_app:
                # Recursive call to handle as update
                return await deploy_app(
                    workspace=workspace,
                    repository=repository,
                    name=name,
                    branch=branch,
                    app_type=app_type,
                    build_strategy=build_strategy,
                    container_port=container_port,
                    cpu=cpu,
                    memory=memory,
                    health_check_path=health_check_path,
                    user=user,
                    environment_slug=environment_slug,
                    git_ref=git_ref,
                    environment_variables=environment_variables,
                    datastore_id=datastore_id,
                    dockerfile_path=dockerfile_path,
                    app_secrets=app_secrets,
                    subdomain=subdomain,
                )
            raise  # Unexpected error, re-raise

    # Check for existing active deployments
    await _check_active_deployments(app)

    # Resolve subdomain (auto-suffix if conflict)
    effective_subdomain = await _resolve_subdomain(
        app_slug=app.slug,
        environment=environment,
        explicit_subdomain=subdomain,
        app_id=str(app.id),
    )

    # Generate image tag
    image_tag = _generate_image_tag(app, git_ref)

    # Create deployment record
    deployment = await Deployment.objects.acreate(
        app=app,
        environment=environment,
        subdomain=effective_subdomain,
        git_ref=git_ref,
        git_commit_sha="",
        git_commit_message="",
        image_tag=image_tag,
        status=Deployment.Status.PENDING,
        status_message="Deployment queued",
        created_by=user,
    )

    # Create initial log entries
    await _create_initial_logs(deployment)

    # Compute URL
    hosted_zone = environment.shared_alb_hosted_zone
    if hosted_zone:
        url = f"https://{effective_subdomain}.{hosted_zone}"
    else:
        url = ""  # No domain configured

    return DeploymentSummary(
        id=str(deployment.id),
        app_id=str(app.id),
        app_name=app.name,
        app_slug=app.slug,
        app_created=app_created,
        environment_name=environment.name,
        subdomain=effective_subdomain,
        url=url,
        git_ref=deployment.git_ref,
        status=deployment.status,
        status_message=deployment.status_message,
        image_tag=deployment.image_tag,
        created_at=deployment.created_at.isoformat(),
    )
