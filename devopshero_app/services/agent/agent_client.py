"""
Configuration for the Claude Agent SDK.

The Claude Agent SDK uses the Claude Code CLI which handles
authentication via environment variables. Supports both:
- Direct Anthropic API (ANTHROPIC_API_KEY)
- AWS Bedrock (CLAUDE_CODE_USE_BEDROCK=1 + AWS credentials)
"""

import os


def _use_anthropic_api() -> bool:
    """Check if direct Anthropic API is configured."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _use_bedrock() -> bool:
    """Check if AWS Bedrock is configured."""
    use_bedrock = os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").lower() in ("1", "true")
    has_region = bool(os.environ.get("AWS_REGION"))
    return use_bedrock and has_region


def is_available() -> bool:
    """
    Check if the Claude Agent SDK is available.

    The SDK works with Claude Code CLI which supports:
    - Direct Anthropic API via ANTHROPIC_API_KEY
    - AWS Bedrock via CLAUDE_CODE_USE_BEDROCK=1 and AWS_REGION

    Returns:
        True if agent can be used (either backend is configured).
    """
    return _use_anthropic_api() or _use_bedrock()
