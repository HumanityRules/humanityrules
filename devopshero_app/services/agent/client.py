"""
Claude client for the deployment agent.

Supports both direct Anthropic API and AWS Bedrock.
Priority: ANTHROPIC_API_KEY > AWS_BEDROCK_REGION
"""

from django.conf import settings

import anthropic


def _use_anthropic_api() -> bool:
    """Check if we should use the direct Anthropic API."""
    return bool(getattr(settings, "ANTHROPIC_API_KEY", None))


def _use_bedrock() -> bool:
    """Check if we should use AWS Bedrock."""
    return bool(getattr(settings, "AWS_BEDROCK_REGION", None))


def get_client() -> anthropic.Anthropic | anthropic.AnthropicBedrock:
    """
    Get a Claude client instance.

    Uses direct Anthropic API if ANTHROPIC_API_KEY is set,
    otherwise uses AWS Bedrock if AWS_BEDROCK_REGION is set.

    Returns:
        Configured Anthropic or AnthropicBedrock client.

    Raises:
        ValueError: If neither backend is configured.
    """
    if _use_anthropic_api():
        return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    if _use_bedrock():
        return anthropic.AnthropicBedrock(aws_region=settings.AWS_BEDROCK_REGION)

    raise ValueError(
        "No Claude backend configured. "
        "Set ANTHROPIC_API_KEY or AWS_BEDROCK_REGION."
    )


def get_model_id() -> str:
    """
    Get the appropriate model ID for the current backend.

    Returns:
        Model ID string (different format for API vs Bedrock).
    """
    if _use_anthropic_api():
        return "claude-opus-4-5-20251101"

    return "anthropic.claude-opus-4-5-20251101-v1:0"


def is_available() -> bool:
    """
    Check if the agent is available (either API key or Bedrock configured).

    Returns:
        True if agent can be used.
    """
    return _use_anthropic_api() or _use_bedrock()
