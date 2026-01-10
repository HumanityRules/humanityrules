"""
Agent service for managing deployment conversations.

This module provides the core agent service that:
- Loads conversation context from the database
- Converts messages to Claude API format
- Sends messages to Claude and handles responses
- Executes tools when requested by Claude
- Saves agent responses back to the database
"""

import json
from pathlib import Path

from django.conf import settings

import anthropic

from devopshero_app.models import Conversation, Message

from . import client
from .tools import ask_user, inspect_repository, list_aws_accounts


# Default model to use for agent conversations
DEFAULT_MODEL = "claude-sonnet-4-20250514"

# Tool definitions for Claude API
TOOLS = [
    {
        "name": "inspect_repository",
        "description": (
            "Analyze a repository's contents to detect application characteristics. "
            "Only file:// URLs are supported (e.g., file:///path/to/repo). "
            "Returns framework, language, Dockerfile info, suggested port, health path, "
            "detected database, and required environment variables."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {
                    "type": "string",
                    "description": (
                        "Repository URL. Only file:// URLs supported. "
                        "Example: file:///app/deployable_repos/flask-app"
                    ),
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to analyze (currently ignored for file:// URLs)",
                },
            },
            "required": ["repo_url", "branch"],
        },
    },
    {
        "name": "list_aws_accounts",
        "description": (
            "List AWS accounts connected to the user's organization. "
            "Use this to find available deployment targets. "
            "Returns account ID, name, status, and region for each account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user a question with interactive choice buttons. "
            "Use this when you need user input to proceed, such as selecting "
            "an AWS account, confirming a deployment, or choosing between options. "
            "The question will be displayed with clickable buttons for each choice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
                "choices": {
                    "type": "array",
                    "description": "List of choices for the user.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Unique identifier for this choice.",
                            },
                            "label": {
                                "type": "string",
                                "description": "Display label for this choice.",
                            },
                            "primary": {
                                "type": "boolean",
                                "description": "Whether this is the recommended choice.",
                            },
                        },
                        "required": ["label"],
                    },
                },
                "allow_text_input": {
                    "type": "boolean",
                    "description": (
                        "Whether to allow the user to type a custom response. "
                        "Default is true."
                    ),
                },
            },
            "required": ["question", "choices"],
        },
    },
]


def _execute_tool(
    tool_name: str,
    tool_input: dict,
    conversation: Conversation,
) -> str:
    """
    Execute a tool and return the result as a string.

    Args:
        tool_name: Name of the tool to execute.
        tool_input: Input parameters for the tool.
        conversation: The conversation context (for accessing user/org).

    Returns:
        JSON string with the tool result.

    Raises:
        ValueError: If tool is unknown.
    """
    if tool_name == "inspect_repository":
        result = inspect_repository(
            repo_url=tool_input["repo_url"],
            branch=tool_input["branch"],
        )
        return json.dumps(result.to_dict(), indent=2)

    elif tool_name == "list_aws_accounts":
        accounts = list_aws_accounts(organization=conversation.organization)
        return json.dumps([a.to_dict() for a in accounts], indent=2)

    elif tool_name == "ask_user":
        result = ask_user(
            question=tool_input["question"],
            choices=tool_input["choices"],
            conversation=conversation,
            allow_text_input=tool_input.get("allow_text_input", True),
        )
        return json.dumps(
            {
                **result.to_dict(),
                "note": (
                    "Question has been presented to the user. "
                    "Wait for their response before proceeding."
                ),
            },
            indent=2,
        )

    else:
        raise ValueError(f"Unknown tool: {tool_name}")


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    return prompt_path.read_text()


def _convert_message_to_claude_format(message: Message) -> dict | None:
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


def _load_conversation_history(conversation: Conversation) -> list[dict]:
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


def process_conversation(conversation: Conversation) -> Message:
    """
    Process a conversation and generate an agent response.

    This is the main entry point for the agent. It:
    1. Loads the conversation history
    2. Sends it to Claude with the system prompt and tools
    3. Handles tool calls in a loop until Claude gives a final response
    4. Saves the agent response to the database
    5. Returns the created Message

    Args:
        conversation: The Conversation to process.

    Returns:
        The final Message created by the agent.

    Raises:
        ValueError: If the conversation has no user messages.
    """
    # Load conversation history
    messages = _load_conversation_history(conversation)

    if not messages:
        raise ValueError("Conversation has no messages to process")

    # Get Claude client
    anthropic_client = client.get_client()

    # Load system prompt
    system_prompt = _load_system_prompt()

    # Track total usage across turns
    total_input_tokens = 0
    total_output_tokens = 0

    # Tool execution loop
    max_iterations = 10  # Prevent infinite loops
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        # Call Claude with tools
        response = anthropic_client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=TOOLS,
        )

        # Track usage
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        # Check if Claude wants to use tools
        if response.stop_reason == "tool_use":
            # Process tool calls
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    tool_id = block.id

                    # Execute the tool
                    try:
                        result = _execute_tool(tool_name, tool_input, conversation)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": result,
                        })
                    except Exception as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": f"Error executing tool: {str(e)}",
                            "is_error": True,
                        })

            # Add assistant message with tool use to history
            messages.append({
                "role": "assistant",
                "content": response.content,
            })

            # Add tool results to history
            messages.append({
                "role": "user",
                "content": tool_results,
            })

            # Continue the loop to get Claude's response to the tool results
            continue

        # No more tool calls - extract the final response
        break

    # Extract the text response
    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text += block.text

    # Determine content type based on response
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
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
            "stop_reason": response.stop_reason,
            "tool_iterations": iteration,
        },
    )

    # Update conversation timestamp
    conversation.save()

    return agent_message
