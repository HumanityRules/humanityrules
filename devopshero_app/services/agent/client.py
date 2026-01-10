"""
Anthropic Bedrock client for the deployment agent.

This module provides access to Claude via AWS Bedrock.
"""

from django.conf import settings

import anthropic


def get_client() -> anthropic.AnthropicBedrock:
    """
    Get an Anthropic Bedrock client instance.

    Uses AWS credentials from environment/CLI automatically.

    Returns:
        Configured AnthropicBedrock client.
    """
    return anthropic.AnthropicBedrock(
        aws_region=settings.AWS_BEDROCK_REGION,
    )


def verify_connection() -> bool:
    """
    Verify that the Bedrock API connection works.

    Makes a minimal API call to verify credentials are valid.

    Returns:
        True if connection is successful.

    Raises:
        botocore.exceptions.ClientError: If AWS credentials are invalid.
    """
    client = get_client()
    # Make a minimal request to verify the connection works
    # Using a tiny max_tokens to minimize cost
    response = client.messages.create(
        model="anthropic.claude-opus-4-5-20251101-v1:0",
        max_tokens=10,
        messages=[{"role": "user", "content": "Hi"}],
    )
    return response.content is not None
