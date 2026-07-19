"""
Tool for listing Route53 hosted zones in a connected AWS account.

This tool allows the agent to discover available domains for app configuration.
"""

from dataclasses import dataclass, asdict

from asgiref.sync import sync_to_async
from django.conf import settings

from humanityrules_app.models import AWSAccount, Environment, Organization
from humanityrules_app.services import infra_customer


@dataclass
class HostedZoneSummary:
    """Summary of a Route53 hosted zone."""

    id: str
    name: str
    record_count: int
    in_use_by_environment: str | None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def _get_hosted_zones_sync(aws_account: AWSAccount) -> list[dict]:
    """Synchronous function to get hosted zones from AWS."""
    session = infra_customer.iam_utils.get_assumed_role_session(
        access_key=settings.HUMR_AWS_ACCESS_KEY,
        secret_key=settings.HUMR_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region="us-east-1",  # Route53 is a global service
    )
    return infra_customer.route53_utils.list_hosted_zones(session=session)


async def list_hosted_zones(aws_account_uuid: str, organization: Organization) -> list[HostedZoneSummary]:
    """
    List Route53 hosted zones in a connected AWS account.

    Args:
        aws_account_uuid: Internal UUID of the AWSAccount record (not the 12-digit AWS account number).
        organization: The Organization (for access validation).

    Returns:
        List of HostedZoneSummary objects.

    Raises:
        ValueError: If AWS account not found or not connected.
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

    # Get hosted zones (boto3 is sync, so wrap it)
    zones = await sync_to_async(_get_hosted_zones_sync)(aws_account)

    # An environment owns its hosted zone exclusively; claimed zones are annotated so
    # the agent offers only unclaimed ones for a new environment.
    claimant_names = {
        environment.shared_alb_hosted_zone: environment.name
        async for environment in Environment.zone_claimants(aws_account=aws_account)
    }

    return [
        HostedZoneSummary(
            id=zone["id"],
            name=zone["name"],
            record_count=zone["record_count"],
            in_use_by_environment=claimant_names.get(zone["name"].rstrip(".")),
        )
        for zone in zones
    ]
