"""
Tool for creating and provisioning environments.

This tool allows the agent to create environments with optional HTTPS configuration.
It provisions VPC, ECS cluster, and shared ALB infrastructure.
"""

from dataclasses import dataclass, asdict

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils.text import slugify

from devopshero_app.models import AWSAccount, Environment, Organization, User
from devopshero_app.services import infra_customer


@dataclass
class EnvironmentSummary:
    """Summary of a created environment."""

    id: str
    name: str
    slug: str
    status: str
    shared_alb_hosted_zone: str | None
    aws_account_id: str
    aws_account_name: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def _provision_environment_sync(
    aws_account: AWSAccount,
    environment: Environment,
    shared_alb_hosted_zone: str | None,
) -> bool:
    """Synchronous function to provision environment infrastructure."""
    session = infra_customer.iam_utils.get_assumed_role_session(
        access_key=settings.DOH_AWS_ACCESS_KEY,
        secret_key=settings.DOH_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region="us-east-1",  # Default region for base infrastructure
    )

    return infra_customer.deploy_base.deploy(
        session=session,
        env_slug=environment.slug,
        synth_only=False,
        shared_alb_hosted_zone=shared_alb_hosted_zone,
    )


async def create_environment(
    aws_account_uuid: str,
    environment_name: str,
    hosted_zone_name: str | None,
    organization: Organization,
    user: User,
) -> EnvironmentSummary:
    """
    Create and provision an environment in an AWS account.

    This provisions VPC, ECS cluster, and shared ALB. If hosted_zone_name
    is provided, also creates wildcard cert for HTTPS.

    The provisioning is synchronous - this tool waits for CloudFormation
    to complete (may take 5-10 minutes for first environment).

    Args:
        aws_account_uuid: Internal UUID of the AWSAccount record.
        environment_name: Human-readable name for the environment (e.g., "default", "staging").
        hosted_zone_name: Hosted zone for wildcard cert (e.g., "dev.example.com"). None = HTTP only.
        organization: The Organization (for access validation).
        user: The User creating the environment.

    Returns:
        EnvironmentSummary with the created environment details.

    Raises:
        ValueError: If AWS account not found, not connected, or environment already exists.
    """
    # Validate AWS account exists and belongs to organization
    try:
        aws_account = await AWSAccount.objects.aget(
            id=aws_account_uuid,
            organization=organization,
        )
    except AWSAccount.DoesNotExist:
        raise ValueError(
            f"AWS account {aws_account_uuid} not found or doesn't belong to your organization."
        )

    # Check account is connected
    if aws_account.status != AWSAccount.Status.CONNECTED:
        raise ValueError(
            f"AWS account '{aws_account.name}' is not connected (status: {aws_account.status}). "
            "Please complete the AWS account connection first."
        )

    # Generate slug from name
    slug = slugify(environment_name)
    if not slug:
        slug = "default"

    # Check if environment already exists
    existing = await Environment.objects.filter(
        aws_account=aws_account,
        slug=slug,
    ).afirst()

    if existing:
        if existing.status == Environment.Status.READY:
            raise ValueError(
                f"Environment '{environment_name}' already exists and is ready. "
                "Use it directly for deployments."
            )
        if existing.status == Environment.Status.PROVISIONING:
            raise ValueError(
                f"Environment '{environment_name}' is currently being provisioned. "
                "Please wait for it to complete."
            )
        if existing.status == Environment.Status.ERROR:
            raise ValueError(
                f"Environment '{environment_name}' exists but failed to provision. "
                f"Error: {existing.status_message}. Please delete it and try again."
            )
        # If PENDING, we can provision it
        environment = existing
    else:
        # Create new environment record
        environment = await Environment.objects.acreate(
            aws_account=aws_account,
            name=environment_name,
            slug=slug,
            status=Environment.Status.PENDING,
            shared_alb_hosted_zone=hosted_zone_name or "",
        )

    # Update status to PROVISIONING
    environment.status = Environment.Status.PROVISIONING
    environment.vpc_stack_name = f"devopshero-{slug}-vpc"
    environment.cluster_stack_name = f"devopshero-{slug}-cluster"
    environment.shared_alb_hosted_zone = hosted_zone_name or ""
    await environment.asave()

    try:
        # Provision infrastructure (synchronous, may take several minutes)
        success = await sync_to_async(_provision_environment_sync)(
            aws_account=aws_account,
            environment=environment,
            shared_alb_hosted_zone=hosted_zone_name,
        )

        if success:
            environment.status = Environment.Status.READY
            await environment.asave()
        else:
            environment.status = Environment.Status.ERROR
            environment.status_message = "Infrastructure deployment failed. Check CloudFormation console for details."
            await environment.asave()
            raise ValueError(
                f"Failed to provision environment '{environment_name}'. "
                "Infrastructure deployment failed."
            )

    except Exception as e:
        environment.status = Environment.Status.ERROR
        environment.status_message = str(e)
        await environment.asave()
        raise ValueError(
            f"Failed to provision environment '{environment_name}': {e}"
        )

    return EnvironmentSummary(
        id=str(environment.id),
        name=environment.name,
        slug=environment.slug,
        status=environment.status,
        shared_alb_hosted_zone=environment.shared_alb_hosted_zone or None,
        aws_account_id=str(aws_account.id),
        aws_account_name=aws_account.name,
    )
