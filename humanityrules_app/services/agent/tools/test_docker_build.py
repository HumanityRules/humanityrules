"""
Tool for testing a Dockerfile by running docker build.

Used by the main deployment agent after generating a Dockerfile to verify
it builds successfully before deploying. Build-only — no push to ECR.

Locally uses the host Docker daemon. In production, delegates to the
remote EC2 builder (same machine used for deployment builds).
"""

import asyncio
import logging
from dataclasses import dataclass

from django.conf import settings

from humanityrules_app.models import Environment, Organization
from humanityrules_app.services import infra_customer
from humanityrules_app.services.agent.sandbox import get_sandbox_paths


logger = logging.getLogger(__name__)

# Truncate build output to this many lines to avoid flooding the LLM context
MAX_OUTPUT_LINES = 80


@dataclass
class TestBuildResult:
    """Result of a test Docker build."""

    success: bool
    build_output: str


def _truncate_output(output: str) -> str:
    """Truncate build output to last N lines for the LLM."""
    lines = output.strip().split("\n")
    if len(lines) > MAX_OUTPUT_LINES:
        return "... (earlier output truncated) ...\n" + "\n".join(lines[-MAX_OUTPUT_LINES:])
    return output.strip()


def _get_aws_session(environment: Environment):
    """Get an AWS session with assumed role credentials for the environment's account."""
    aws_account = environment.aws_account
    return infra_customer.iam_utils.get_assumed_role_session(
        access_key=settings.HUMR_AWS_ACCESS_KEY,
        secret_key=settings.HUMR_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=environment.aws_region,
    )


async def test_docker_build(conversation_id, organization: Organization, environment_slug: str) -> TestBuildResult:
    """Run a test Docker build for the Dockerfile in the conversation's sandbox."""
    source_path = get_sandbox_paths(conversation_id).src_path

    if not (source_path / "Dockerfile").exists():
        return TestBuildResult(
            success=False,
            build_output="No Dockerfile found in the repository root.",
        )

    # Look up environment and get AWS session for remote builder
    environment = await Environment.objects.filter(
        aws_account__organization=organization,
        slug=environment_slug,
    ).select_related("aws_account").afirst()

    if not environment:
        return TestBuildResult(
            success=False,
            build_output=f"Environment '{environment_slug}' not found. Cannot run test build.",
        )

    session = await asyncio.to_thread(
        _get_aws_session,
        environment,
    )

    success, output = await asyncio.to_thread(
        infra_customer.ecr_utils.test_docker_build,
        session=session,
        env_slug=environment_slug,
        source_path=source_path,
    )

    return TestBuildResult(
        success=success,
        build_output=_truncate_output(output),
    )
