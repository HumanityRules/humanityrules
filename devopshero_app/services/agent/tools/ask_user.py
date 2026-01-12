"""
Tool for asking the user interactive questions.

This tool enables human-in-the-loop interactions where the agent
presents choices to the user and waits for their response.
"""

import uuid
from dataclasses import dataclass, asdict

from devopshero_app.models import Conversation, Message


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
    """Result of presenting a question to the user."""

    message_id: str
    status: str = "awaiting_response"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


async def ask_user(
    question: str,
    choices: list[dict],
    conversation: Conversation,
    allow_text_input: bool,
) -> AskUserResult:
    """
    Present a question to the user with choices.

    This creates a CHOICE message in the conversation that renders
    as interactive buttons in the UI. The user can click a choice
    or type a custom response (if allow_text_input is True).

    Args:
        question: The question to ask the user.
        choices: List of choice dicts with 'id', 'label', and optional 'primary'.
        conversation: The Conversation context.
        allow_text_input: Whether to allow free text input.

    Returns:
        AskUserResult indicating the question was presented.
    """
    # Ensure each choice has an ID
    processed_choices = []
    for i, choice in enumerate(choices):
        processed_choice = {
            "id": choice.get("id", str(uuid.uuid4())),
            "label": choice["label"],
            "primary": choice.get("primary", i == 0),  # First is primary by default
        }
        processed_choices.append(processed_choice)

    # Create the CHOICE message
    message = await Message.objects.acreate(
        conversation=conversation,
        role=Message.Role.AGENT,
        content_type=Message.ContentType.CHOICE,
        content=question,
        metadata={
            "choices": processed_choices,
            "allow_text": allow_text_input,
        },
    )

    return AskUserResult(
        message_id=str(message.id),
        status="awaiting_response",
    )
