"""
Tool for creating managed datastores within a workspace.

This tool allows the agent to create database configurations
that will be provisioned as Aurora Serverless v2 clusters.
"""

from dataclasses import dataclass, asdict

from django.utils.text import slugify

from devopshero_app.models import Datastore, Organization, User, Workspace


@dataclass
class DatastoreSummary:
    """Summary of a created datastore."""

    id: str
    name: str
    slug: str
    workspace_id: str
    workspace_name: str
    engine: str
    database_name: str
    deployment_mode: str
    serverless_min_acu: float | None
    serverless_max_acu: float | None
    status: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def create_datastore(
    workspace_id: str,
    name: str,
    engine: str,
    database_name: str,
    organization: Organization,
    user: User,
    deployment_mode: str,
    serverless_min_acu: float,
    serverless_max_acu: float,
) -> DatastoreSummary:
    """
    Create a managed database in a workspace.

    Args:
        workspace_id: UUID of the workspace to create the datastore in.
        name: Human-readable name for the datastore.
        engine: Database engine (aurora-mysql, aurora-postgresql).
        database_name: Name of the database to create.
        organization: The Organization this datastore belongs to.
        user: The User creating the datastore.
        deployment_mode: Deployment mode (aurora_serverless_v2, aurora_provisioned).
        serverless_min_acu: Minimum ACUs for serverless mode.
        serverless_max_acu: Maximum ACUs for serverless mode.

    Returns:
        DatastoreSummary with the created datastore details.

    Raises:
        ValueError: If workspace doesn't exist, doesn't belong to org,
                    or other validation fails.
    """
    # Validate workspace exists and belongs to organization
    try:
        workspace = await Workspace.objects.aget(
            id=workspace_id,
            organization=organization,
        )
    except Workspace.DoesNotExist:
        raise ValueError(
            f"Workspace {workspace_id} not found or doesn't belong to your organization."
        )

    # Validate engine
    valid_engines = [choice[0] for choice in Datastore.Engine.choices]
    if engine not in valid_engines:
        raise ValueError(
            f"Invalid engine '{engine}'. Must be one of: {', '.join(valid_engines)}"
        )

    # Validate deployment_mode
    valid_modes = [choice[0] for choice in Datastore.DeploymentMode.choices]
    if deployment_mode not in valid_modes:
        raise ValueError(
            f"Invalid deployment_mode '{deployment_mode}'. Must be one of: {', '.join(valid_modes)}"
        )

    # Validate ACU values for serverless
    if deployment_mode == Datastore.DeploymentMode.SERVERLESS_V2:
        if serverless_min_acu < 0.5:
            raise ValueError("Minimum ACU for Aurora Serverless v2 is 0.5")
        if serverless_max_acu > 128:
            raise ValueError("Maximum ACU for Aurora Serverless v2 is 128")
        if serverless_min_acu > serverless_max_acu:
            raise ValueError("Minimum ACU cannot be greater than maximum ACU")

    # Generate unique slug within workspace
    base_slug = slugify(name)
    slug = base_slug
    counter = 1
    while await Datastore.objects.filter(workspace=workspace, slug=slug).aexists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Create the datastore
    datastore = await Datastore.objects.acreate(
        workspace=workspace,
        name=name,
        slug=slug,
        engine=engine,
        deployment_mode=deployment_mode,
        serverless_min_acu=serverless_min_acu if deployment_mode == Datastore.DeploymentMode.SERVERLESS_V2 else None,
        serverless_max_acu=serverless_max_acu if deployment_mode == Datastore.DeploymentMode.SERVERLESS_V2 else None,
        database_name=database_name,
        status=Datastore.Status.PENDING,
        created_by=user,
    )

    return DatastoreSummary(
        id=str(datastore.id),
        name=datastore.name,
        slug=datastore.slug,
        workspace_id=str(workspace.id),
        workspace_name=workspace.name,
        engine=datastore.engine,
        database_name=datastore.database_name,
        deployment_mode=datastore.deployment_mode,
        serverless_min_acu=datastore.serverless_min_acu,
        serverless_max_acu=datastore.serverless_max_acu,
        status=datastore.status,
    )
