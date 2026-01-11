"""
Agent service for managing deployment conversations.

This module provides the core agent service that:
- Loads conversation context from the database
- Sends messages to Claude using the Claude Agent SDK
- Handles tool calls automatically via MCP tools
- Saves agent responses back to the database

Built on the Claude Agent SDK for robust agent orchestration with
structured tool calling, conversation memory, and streaming responses.
"""

import asyncio
import os
from pathlib import Path

from asgiref.sync import sync_to_async
from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
)
from claude_agent_sdk.types import TextBlock

from devopshero_app.models import Conversation, Message

from .mcp_tools import (
    conversation_context,
    devopshero_mcp_server,
    TOOL_NAMES,
)


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    return prompt_path.read_text()


def _get_claude_env() -> dict[str, str]:
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
    use_bedrock = os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").lower() in (
        "1",
        "true",
    )

    if use_bedrock:
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


def _get_last_user_message(conversation: Conversation) -> str:
    """
    Get the last user message from a conversation.

    Args:
        conversation: The Conversation model instance.

    Returns:
        The content of the last user message.

    Raises:
        ValueError: If no user messages found.
    """
    messages = conversation.messages.filter(role="user").order_by("-created_at")
    last_message = messages.first()

    if not last_message:
        raise ValueError("Conversation has no user messages to process")

    return last_message.content


async def _process_conversation_async(conversation: Conversation) -> Message:
    """
    Process a conversation asynchronously using the Claude Agent SDK.

    This is the async implementation that:
    1. Sets the conversation context for tools
    2. Creates a ClaudeSDKClient with our MCP tools
    3. Sends the user's message
    4. Collects the agent's response
    5. Saves and returns the agent message

    Args:
        conversation: The Conversation to process.

    Returns:
        The final Message created by the agent.
    """
    # Set the conversation context for tools to access
    conversation_context.set(conversation)

    # Get the last user message to send
    user_message = await sync_to_async(_get_last_user_message)(conversation)

    # Load system prompt
    system_prompt = _load_system_prompt()

    # Configure the SDK client
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"devopshero": devopshero_mcp_server},
        allowed_tools=TOOL_NAMES,
        # Accept tool usage automatically (tools handle their own side effects)
        permission_mode="bypassPermissions",
        # Pass Claude-specific AWS credentials to the subprocess
        env=_get_claude_env(),
    )

    # Track response content and metadata
    response_text = ""
    total_cost_usd = None
    usage = None
    num_turns = 0
    session_id = None

    async with ClaudeSDKClient(options=options) as client:
        # Send the user's message
        await client.query(user_message)

        # Process the response stream
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                # Extract text content from the assistant's response
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text

            elif isinstance(message, ResultMessage):
                # Capture final metadata
                total_cost_usd = message.total_cost_usd
                usage = message.usage
                num_turns = message.num_turns
                session_id = message.session_id

    # Save agent response to database
    agent_message = await sync_to_async(Message.objects.create)(
        conversation=conversation,
        role=Message.Role.AGENT,
        content_type=Message.ContentType.TEXT,
        content=response_text,
        metadata={
            "sdk": "claude-agent-sdk",
            "session_id": session_id,
            "usage": usage,
            "total_cost_usd": total_cost_usd,
            "num_turns": num_turns,
        },
    )

    # Update conversation timestamp
    await sync_to_async(conversation.save)()

    return agent_message


def process_conversation(conversation: Conversation) -> Message:
    """
    Process a conversation and generate an agent response.

    This is the main entry point for the agent. It:
    1. Sets up the conversation context for MCP tools
    2. Sends the latest user message to Claude via the Agent SDK
    3. Handles tool calls automatically through the SDK
    4. Saves the agent response to the database
    5. Returns the created Message

    Args:
        conversation: The Conversation to process.

    Returns:
        The final Message created by the agent.

    Raises:
        ValueError: If the conversation has no user messages.
    """
    return asyncio.run(_process_conversation_async(conversation))
