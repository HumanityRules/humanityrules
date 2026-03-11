"""
Tool for provisioning environments.

This tool provisions an Environment by creating (or resetting) a record with
PENDING status. The job worker picks up pending environments and provisions
them via the environment_executor.

Retry semantics: if the environment previously failed (ERROR status), calling
this tool again resets it to PENDING and retries provisioning.
"""

from dataclasses import dataclass, asdict

from django.utils.text import slugify

from devopshero_app.models import AWSAccount, Environment, Organization, User


@dataclass
class EnvironmentSummary:
    """Summary of a provisioned environment."""

    id: str
    name: str
    slug: str
    aws_region: str
    status: str
    shared_alb_hosted_zone: str | None
    aws_account_id: str
    aws_account_name: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def provision_environment(
    aws_account_uuid: str,
    environment_name: str,
    aws_region: str,
    hosted_zone_name: str | None,
    organization: Organization,
    user: User,
) -> EnvironmentSummary:
    """
    Provision an environment in an AWS account.

    Creates an Environment record with status PENDING (or resets a failed one).
    The job worker picks it up and provisions VPC, ECS cluster, and shared ALB.
    If hosted_zone_name is provided, also creates wildcard cert for HTTPS.

    Retry semantics: if the environment previously failed (ERROR status),
    resets it to PENDING and allows the job worker to retry provisioning.

    Use get_environment_status to poll for provisioning progress.
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
                "Use get_environment_status to check progress."
            )
        if existing.status == Environment.Status.PENDING:
            raise ValueError(
                f"Environment '{environment_name}' is already queued for provisioning. "
                "Use get_environment_status to check progress."
            )
        if existing.status == Environment.Status.ERROR:
            # Reset to PENDING so the job worker retries provisioning.
            # Allow updating config (region, hosted zone) on retry.
            existing.status = Environment.Status.PENDING
            existing.status_message = ""
            existing.name = environment_name
            existing.aws_region = aws_region
            existing.shared_alb_hosted_zone = hosted_zone_name or ""
            await existing.asave()

            return EnvironmentSummary(
                id=str(existing.id),
                name=existing.name,
                slug=existing.slug,
                aws_region=existing.aws_region,
                status=existing.status,
                shared_alb_hosted_zone=existing.shared_alb_hosted_zone or None,
                aws_account_id=str(aws_account.id),
                aws_account_name=aws_account.name,
            )

    # Create new environment record with PENDING status
    environment = await Environment.objects.acreate(
        aws_account=aws_account,
        name=environment_name,
        slug=slug,
        aws_region=aws_region,
        status=Environment.Status.PENDING,
        vpc_stack_name=f"devopshero-{slug}-vpc",
        cluster_stack_name=f"devopshero-{slug}-cluster",
        shared_alb_hosted_zone=hosted_zone_name or "",
    )

    return EnvironmentSummary(
        id=str(environment.id),
        name=environment.name,
        slug=environment.slug,
        aws_region=environment.aws_region,
        status=environment.status,
        shared_alb_hosted_zone=environment.shared_alb_hosted_zone or None,
        aws_account_id=str(aws_account.id),
        aws_account_name=aws_account.name,
    )
