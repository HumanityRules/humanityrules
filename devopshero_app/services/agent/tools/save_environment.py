"""
Tool for creating or updating an environment setup draft.

If the conversation has no context_environment, creates a new Environment draft and sets it.
If context_environment is already set, updates the existing draft/error environment.
"""

from dataclasses import asdict, dataclass

from django.utils.text import slugify

import devopshero_app.models as models


EDITABLE_ENVIRONMENT_STATUSES = [
    models.Environment.Status.DRAFT,
    models.Environment.Status.ERROR,
]


def _build_stack_name(slug: str, stack_kind: str) -> str:
    """Build the CloudFormation stack name for an environment resource."""
    return f"devopshero-{slug}-{stack_kind}"


def _normalize_hosted_zone_name(hosted_zone_name: str) -> str:
    """Normalize hosted zone input for storage."""
    return hosted_zone_name.strip().rstrip(".")


@dataclass
class SaveEnvironmentResult:
    """Result of save_environment operation."""

    id: str
    name: str
    slug: str
    aws_region: str
    status: str
    shared_alb_hosted_zone: str | None
    aws_account_id: str
    aws_account_name: str
    created: bool

    def to_dict(self) -> dict[str, str | bool | None]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def save_environment(
    conversation: models.Conversation,
    aws_account: models.AWSAccount,
    environment_name: str,
    aws_region: str,
    hosted_zone_name: str,
) -> SaveEnvironmentResult:
    """Create or update an Environment draft based on conversation context."""
    slug = slugify(environment_name)
    if not slug:
        raise ValueError(f"Invalid environment name '{environment_name}': cannot generate slug.")

    normalized_hosted_zone_name = _normalize_hosted_zone_name(hosted_zone_name=hosted_zone_name)
    existing_environment_id = conversation.context_environment_id

    if existing_environment_id:
        try:
            environment = await models.Environment.objects.select_related("aws_account").aget(
                id=existing_environment_id,
                aws_account=aws_account,
            )
        except models.Environment.DoesNotExist:
            raise ValueError("The current environment does not exist or belongs to a different AWS account.")
        if environment.status not in EDITABLE_ENVIRONMENT_STATUSES:
            raise ValueError(
                f"Environment '{environment.name}' is in '{environment.status}' state and cannot be edited. "
                "Only draft or error environments can be updated."
            )

        attempted_provisioning = environment.status == models.Environment.Status.ERROR
        environment.name = environment_name
        environment.aws_region = aws_region
        environment.shared_alb_hosted_zone = normalized_hosted_zone_name
        environment.status = models.Environment.Status.DRAFT
        environment.status_message = "Ready to provision"
        environment.vpc_id = ""
        environment.cluster_arn = ""

        if not attempted_provisioning:
            environment.slug = slug
            environment.vpc_stack_name = _build_stack_name(slug=slug, stack_kind="vpc")
            environment.cluster_stack_name = _build_stack_name(slug=slug, stack_kind="cluster")

        await environment.asave()
        created = False
    else:
        existing_environment = await models.Environment.objects.filter(
            aws_account=aws_account,
            slug=slug,
        ).afirst()

        if existing_environment and existing_environment.status != models.Environment.Status.DISCARDED:
            raise ValueError(
                f"Environment '{environment_name}' already exists in AWS account '{aws_account.name}'. "
                "Resume its setup or choose a different name."
            )

        if existing_environment:
            environment = existing_environment
            environment.name = environment_name
            environment.slug = slug
            environment.aws_region = aws_region
            environment.status = models.Environment.Status.DRAFT
            environment.status_message = "Ready to provision"
            environment.vpc_stack_name = _build_stack_name(slug=slug, stack_kind="vpc")
            environment.cluster_stack_name = _build_stack_name(slug=slug, stack_kind="cluster")
            environment.vpc_id = ""
            environment.cluster_arn = ""
            environment.shared_alb_hosted_zone = normalized_hosted_zone_name
            await environment.asave()
        else:
            environment = await models.Environment.objects.acreate(
                aws_account=aws_account,
                name=environment_name,
                slug=slug,
                aws_region=aws_region,
                status=models.Environment.Status.DRAFT,
                status_message="Ready to provision",
                vpc_stack_name=_build_stack_name(slug=slug, stack_kind="vpc"),
                cluster_stack_name=_build_stack_name(slug=slug, stack_kind="cluster"),
                shared_alb_hosted_zone=normalized_hosted_zone_name,
            )

        conversation.context_environment = environment
        await conversation.asave(update_fields=["context_environment", "updated_at"])
        created = True

    return SaveEnvironmentResult(
        id=str(environment.id),
        name=environment.name,
        slug=environment.slug,
        aws_region=environment.aws_region,
        status=environment.status,
        shared_alb_hosted_zone=environment.shared_alb_hosted_zone or None,
        aws_account_id=str(aws_account.id),
        aws_account_name=aws_account.name,
        created=created,
    )
