"""
Tool for provisioning environments.

This tool queues provisioning for a saved Environment draft.
The job worker picks up pending environments and provisions them via the
environment_executor.
"""

from dataclasses import dataclass, asdict

import humanityrules_app.models as models
from humanityrules_app.services.jobs import environment_operation_gate


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

    def to_dict(self) -> dict[str, str | None]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def provision_environment(
    conversation: models.Conversation,
    organization: models.Organization,
) -> EnvironmentSummary:
    """
    Queue provisioning for the conversation's current environment draft.

    Use get_environment_status to poll for provisioning progress.
    """
    if not conversation.context_environment_id:
        raise ValueError(
            "No environment context set. Use save_environment first to create or update the draft."
        )

    try:
        environment = await models.Environment.objects.select_related("aws_account").aget(
            id=conversation.context_environment_id,
            aws_account__organization=organization,
        )
    except models.Environment.DoesNotExist:
        raise ValueError(
            f"Environment {conversation.context_environment_id} not found or doesn't belong to your organization."
        )

    aws_account = environment.aws_account
    if aws_account.status != models.AWSAccount.Status.CONNECTED:
        raise ValueError(
            f"AWS account '{aws_account.name}' is not connected (status: {aws_account.status}). "
            "Please complete the AWS account connection first."
        )

    if environment.status == models.Environment.Status.READY:
        raise ValueError(
            f"Environment '{environment.name}' is already ready. "
            "Use it directly for deployments."
        )

    if environment.status == models.Environment.Status.PROVISIONING:
        raise ValueError(
            f"Environment '{environment.name}' is currently being provisioned. "
            "Use get_environment_status to check progress."
        )

    if environment.status == models.Environment.Status.PENDING:
        raise ValueError(
            f"Environment '{environment.name}' is already queued for provisioning. "
            "Use get_environment_status to check progress."
        )

    if environment.status not in (models.Environment.Status.DRAFT, models.Environment.Status.ERROR):
        raise ValueError(
            f"Environment '{environment.name}' is in '{environment.status}' state and cannot be provisioned. "
            "Only draft or error environments can be queued."
        )

    previous_status = environment.status
    transitioned = await environment_operation_gate.atransition_environment_status(
        environment_id=environment.id,
        expected_statuses=(previous_status,),
        new_status=models.Environment.Status.PENDING,
        status_message="Queued for provisioning",
    )
    if not transitioned:
        current_status = await models.Environment.objects.values_list("status", flat=True).aget(id=environment.id)
        raise ValueError(
            f"Environment '{environment.name}' changed to '{current_status}' while provisioning was being queued. "
            "Review its current state and try again."
        )

    environment.status = models.Environment.Status.PENDING
    environment.status_message = "Queued for provisioning"

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
