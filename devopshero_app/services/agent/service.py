"""
Agent service for managing deployment conversations.

This module provides the core agent service that:
- Loads conversation context from the database
- Converts messages to Claude API format
- Sends messages to Claude and handles responses
- Saves agent responses back to the database
"""

from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

import anthropic

from . import client

if TYPE_CHECKING:
    from devopshero_app.models import Conversation, Message


# Default model to use for agent conversations
DEFAULT_MODEL = "claude-sonnet-4-20250514"


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    return prompt_path.read_text()


def _convert_message_to_claude_format(message: "Message") -> dict | None:
    """
    Convert a database Message to Claude API format.

    Args:
        message: The Message model instance.

    Returns:
        Dict in Claude message format, or None if message should be skipped.
    """
    # Map our roles to Claude roles
    role_map = {
        "user": "user",
        "agent": "assistant",
    }

    # Skip system messages in conversation history
    # (they're deployment logs, progress, etc. - not conversational)
    if message.role == "system":
        return None

    claude_role = role_map.get(message.role)
    if not claude_role:
        return None

    return {
        "role": claude_role,
        "content": message.content,
    }


def _load_conversation_history(conversation: "Conversation") -> list[dict]:
    """
    Load conversation history in Claude API format.

    Args:
        conversation: The Conversation model instance.

    Returns:
        List of messages in Claude API format.
    """
    messages = conversation.messages.all().order_by("created_at")
    claude_messages = []

    for message in messages:
        converted = _convert_message_to_claude_format(message)
        if converted:
            claude_messages.append(converted)

    return claude_messages


def process_conversation(conversation: "Conversation") -> "Message":
    """
    Process a conversation and generate an agent response.

    This is the main entry point for the agent. It:
    1. Loads the conversation history
    2. Sends it to Claude with the system prompt
    3. Handles the response (including any tool calls)
    4. Saves the agent response to the database
    5. Returns the created Message

    Args:
        conversation: The Conversation to process.

    Returns:
        The Message created by the agent.

    Raises:
        ValueError: If the conversation has no user messages.
    """
    # Import here to avoid circular imports
    from devopshero_app.models import Message

    # Load conversation history
    history = _load_conversation_history(conversation)

    if not history:
        raise ValueError("Conversation has no messages to process")

    # Get Claude client
    anthropic_client = client.get_client()

    # Load system prompt
    system_prompt = _load_system_prompt()

    # Call Claude
    response = anthropic_client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=history,
    )

    # Extract the text response
    # For now, we assume a simple text response (no tool calls yet)
    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text += block.text

    # Determine content type based on response
    # For now, default to markdown since Claude often uses formatting
    content_type = Message.ContentType.MARKDOWN

    # Save agent response to database
    agent_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.AGENT,
        content_type=content_type,
        content=response_text,
        metadata={
            "model": DEFAULT_MODEL,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "stop_reason": response.stop_reason,
        },
    )

    # Update conversation timestamp
    conversation.save()

    return agent_message
