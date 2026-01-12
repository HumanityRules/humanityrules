"""
Tool for asking the user interactive questions.

This tool enables human-in-the-loop interactions where the agent
presents choices to the user and waits for their response.
"""

import json
import uuid
from dataclasses import dataclass, asdict

from devopshero_app.models import Conversation, Message


def normalize_choices(choices: list[dict] | str) -> list[dict]:
    """
    Normalize choices input to a list of dicts.

    Handles:
    - JSON string: Parse to list
    - List of strings: Convert each to {"label": string}
    - List of dicts: Return as-is

    Args:
        choices: Raw choices from model (JSON string, list of strings, or list of dicts)

    Returns:
        List of choice dicts with at least a "label" key.
    """
    # Parse JSON string if needed
    if isinstance(choices, str):
        choices = json.loads(choices)

    # Convert string items to dicts
    normalized = []
    for choice in choices:
        if isinstance(choice, str):
            normalized.append({"label": choice})
        else:
            normalized.append(choice)

    return normalized


@dataclass
class Choice:
    """A single choice option for the user."""

    id: str
    label: str
    primary: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class AskUserResult:
    """
    Result of preparing a question for the user.

    Note: The CHOICE message is NOT created by this tool. Instead, the
    prepared data is returned so that agent_service can create the message
    at the right time for proper ordering (after TEXT, before response).
    """

    message_id: str
    status: str = "awaiting_response"
    # Deferred message data - agent_service will create the actual message
    deferred_choice: dict | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def ask_user(
    question: str,
    choices: list[dict] | str,
    conversation: Conversation,
    allow_text_input: bool,
) -> AskUserResult:
    """
    Prepare a question for the user with choices.

    This does NOT create the CHOICE message directly. Instead, it returns
    the prepared data so agent_service can create the message at the right
    time for proper ordering (TEXT before TOOL_CALL before CHOICE).

    Args:
        question: The question to ask the user.
        choices: List of choice dicts, list of strings, or JSON string.
        conversation: The Conversation context (unused, kept for API compat).
        allow_text_input: Whether to allow free text input.

    Returns:
        AskUserResult with deferred_choice data for agent_service to create.
    """
    # Normalize choices to list of dicts
    normalized_choices = normalize_choices(choices)

    # Ensure each choice has an ID
    processed_choices = []
    for i, choice in enumerate(normalized_choices):
        processed_choice = {
            "id": choice.get("id", str(uuid.uuid4())),
            "label": choice["label"],
            "primary": choice.get("primary", i == 0),  # First is primary by default
        }
        processed_choices.append(processed_choice)

    # Pre-generate message ID (message will be created by agent_service)
    message_id = str(uuid.uuid4())

    # Return deferred choice data - agent_service will create the actual message
    return AskUserResult(
        message_id=message_id,
        status="awaiting_response",
        deferred_choice={
            "content": question,
            "metadata": {
                "choices": processed_choices,
                "allow_text": allow_text_input,
            },
        },
    )
