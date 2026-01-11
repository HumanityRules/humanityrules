"""
Configuration for the Claude Agent SDK.

The Claude Agent SDK uses the Claude Code runtime which handles
authentication via environment variables. Supports both:
- Direct Anthropic API (ANTHROPIC_API_KEY)
- AWS Bedrock (CLAUDE_CODE_USE_BEDROCK=1 + AWS_BEDROCK_* credentials)
"""

import os


def _use_anthropic_api() -> bool:
    """Check if direct Anthropic API is configured."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _use_bedrock() -> bool:
    """
    Check if AWS Bedrock is configured for Claude.

    Requires CLAUDE_CODE_USE_BEDROCK=1 and AWS_BEDROCK_REGION.
    """
    use_bedrock = os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").lower() in (
        "1",
        "true",
    )
    has_region = bool(os.environ.get("AWS_BEDROCK_REGION"))
    return use_bedrock and has_region


def is_available() -> bool:
    """
    Check if the Claude Agent SDK is available.

    The SDK works with Claude Code which supports:
    - Direct Anthropic API via ANTHROPIC_API_KEY
    - AWS Bedrock via CLAUDE_CODE_USE_BEDROCK=1 + AWS_BEDROCK_REGION

    Returns:
        True if agent can be used (either backend is configured).
    """
    return _use_anthropic_api() or _use_bedrock()


def get_claude_env() -> dict[str, str]:
    """
    Build environment variables for the Claude Code subprocess.

    Maps AWS_BEDROCK_* env vars to the AWS_* vars that Claude Code expects.
    This allows using separate credentials for Claude Code vs the main app.

    Env var mapping:
    - AWS_BEDROCK_REGION -> AWS_REGION
    - AWS_BEDROCK_ACCESS_KEY_ID -> AWS_ACCESS_KEY_ID
    - AWS_BEDROCK_SECRET_ACCESS_KEY -> AWS_SECRET_ACCESS_KEY
    - CLAUDE_CODE_USE_BEDROCK -> CLAUDE_CODE_USE_BEDROCK (passed through)

    Returns:
        Dict of environment variables to pass to Claude Code.
    """
    env: dict[str, str] = {}

    # Check if Bedrock is enabled
    if _use_bedrock():
        env["CLAUDE_CODE_USE_BEDROCK"] = "1"

        # Map AWS_BEDROCK_REGION -> AWS_REGION
        region = os.environ.get("AWS_BEDROCK_REGION")
        if region:
            env["AWS_REGION"] = region

        # Map AWS_BEDROCK_ACCESS_KEY_ID -> AWS_ACCESS_KEY_ID
        access_key = os.environ.get("AWS_BEDROCK_ACCESS_KEY_ID")
        if access_key:
            env["AWS_ACCESS_KEY_ID"] = access_key

        # Map AWS_BEDROCK_SECRET_ACCESS_KEY -> AWS_SECRET_ACCESS_KEY
        secret_key = os.environ.get("AWS_BEDROCK_SECRET_ACCESS_KEY")
        if secret_key:
            env["AWS_SECRET_ACCESS_KEY"] = secret_key

    return env
