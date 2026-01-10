"""
Anthropic API client for the deployment agent.

This module provides access to the Anthropic Claude API.
"""

from django.conf import settings

import anthropic


def get_client() -> anthropic.Anthropic:
    """
    Get an Anthropic client instance.

    Returns:
        Configured Anthropic client.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not configured.
    """
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not configured. "
            "Please set it in your .env file."
        )
    return anthropic.Anthropic(api_key=api_key)


def verify_connection() -> bool:
    """
    Verify that the Anthropic API connection works.

    Makes a minimal API call to verify credentials are valid.

    Returns:
        True if connection is successful.

    Raises:
        anthropic.AuthenticationError: If API key is invalid.
        anthropic.APIConnectionError: If connection fails.
    """
    client = get_client()
    # Make a minimal request to verify the API key works
    # Using a tiny max_tokens to minimize cost
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=10,
        messages=[{"role": "user", "content": "Hi"}],
    )
    return response.content is not None
