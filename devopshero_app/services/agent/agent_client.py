"""
Configuration for the Claude Agent SDK.

The Claude Agent SDK uses the Claude Code runtime which handles
authentication via environment variables. Supports both:
- Direct Anthropic API (ANTHROPIC_API_KEY)
- AWS Bedrock (CLAUDE_AWS_REGION + AWS credentials)

For Bedrock, you can use CLAUDE_AWS_PROFILE to specify an AWS profile
specifically for Claude Code without affecting the main app.
"""

import os

from django.conf import settings as django_settings


def _use_anthropic_api() -> bool:
    """Check if direct Anthropic API is configured."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _use_bedrock() -> bool:
    """
    Check if AWS Bedrock is configured for Claude.

    Checks for Claude-specific settings first, then falls back to generic ones.
    """
    # Check Claude-specific region setting
    claude_region = getattr(
        django_settings, "CLAUDE_AWS_REGION", None
    ) or os.environ.get("CLAUDE_AWS_REGION")
    if claude_region:
        return True

    # Fall back to generic Bedrock settings
    use_bedrock = os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").lower() in (
        "1",
        "true",
    )
    has_region = bool(os.environ.get("AWS_REGION"))
    return use_bedrock and has_region


def is_available() -> bool:
    """
    Check if the Claude Agent SDK is available.

    The SDK works with Claude Code which supports:
    - Direct Anthropic API via ANTHROPIC_API_KEY
    - AWS Bedrock via CLAUDE_AWS_REGION (or CLAUDE_CODE_USE_BEDROCK + AWS_REGION)

    Returns:
        True if agent can be used (either backend is configured).
    """
    return _use_anthropic_api() or _use_bedrock()
