"""
Lightweight LLM client for simple completions.

Supports both direct Anthropic API and AWS Bedrock, mirroring the
configuration pattern from agent_client.py.

Priority: ANTHROPIC_API_KEY > Bedrock credentials
"""

from django.conf import settings

import anthropic


# Model aliases mapped to (API model ID, Bedrock model ID)
# Use these aliases in CLAUDE_MODEL_GENERAL / CLAUDE_MODEL_ENVIRONMENT / CLAUDE_MODEL_APP_DEPLOYMENT env vars
# See: https://docs.anthropic.com/en/docs/about-claude/models/overview
LLM_MODELS: dict[str, tuple[str, str]] = {
    "opus-4.6": ("claude-opus-4-6-v1", "us.anthropic.claude-opus-4-6-v1"),
    "opus-4.5": ("claude-opus-4-5-20251101", "us.anthropic.claude-opus-4-5-20251101-v1:0"),    
    "sonnet-4.5": ("claude-sonnet-4-5-20250929", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    "haiku-4.5": ("claude-haiku-4-5-20251001", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
}


def _use_anthropic_api() -> bool:
    """Check if direct Anthropic API is configured."""
    return bool(getattr(settings, "ANTHROPIC_API_KEY", None))


def get_client() -> anthropic.Anthropic | anthropic.AnthropicBedrock:
    """
    Get a Claude client instance.

    Uses direct Anthropic API if ANTHROPIC_API_KEY is set,
    otherwise falls back to AWS Bedrock.

    Returns:
        Configured Anthropic or AnthropicBedrock client.
    """
    if _use_anthropic_api():
        return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    # Fall back to Bedrock with explicit credentials if provided
    return anthropic.AnthropicBedrock(
        aws_region=getattr(settings, "AWS_BEDROCK_REGION", "us-east-1"),
        aws_access_key=getattr(settings, "AWS_BEDROCK_ACCESS_KEY_ID", None),
        aws_secret_key=getattr(settings, "AWS_BEDROCK_SECRET_ACCESS_KEY", None),
    )


def get_model_id(alias: str) -> str:
    """
    Get the model ID for an alias, formatted for the current backend.

    Args:
        alias: Simple model alias (e.g., "opus-4.5", "haiku-4.5")

    Returns:
        Model ID string in correct format for API or Bedrock.

    Raises:
        ValueError: If alias is not recognized.
    """
    if alias not in LLM_MODELS:
        raise ValueError(f"Unknown model alias: {alias}. Available: {list(LLM_MODELS.keys())}")

    api_id, bedrock_id = LLM_MODELS[alias]
    return api_id if _use_anthropic_api() else bedrock_id
