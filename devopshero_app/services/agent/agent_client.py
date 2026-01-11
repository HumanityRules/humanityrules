"""
Configuration for the Claude Agent SDK.

The Claude Agent SDK uses the Claude Code CLI which handles
authentication via the ANTHROPIC_API_KEY environment variable.
"""

import os


def is_available() -> bool:
    """
    Check if the Claude Agent SDK is available.

    The SDK requires ANTHROPIC_API_KEY to be set in the environment.
    The Claude Code CLI handles authentication automatically.

    Returns:
        True if agent can be used (API key is configured).
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
