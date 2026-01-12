"""
Agent service for managing deployment conversations.

This module provides the core agent service that:
- Loads conversation context from the database
- Sends messages to Claude using the Claude Agent SDK
- Handles tool calls automatically via MCP tools
- Saves agent responses back to the database
- Records tool calls as visible messages in the conversation

Built on the Claude Agent SDK for robust agent orchestration with
structured tool calling, conversation memory, and streaming responses.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    UserMessage,
)
from claude_agent_sdk.types import (
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    SystemMessage,
    StreamEvent,
)

from devopshero_app.models import Conversation, Message

from .agent_client import get_claude_env
from .mcp_tools import (
    conversation_context,
    devopshero_mcp_server,
    TOOL_NAMES,
)


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    return prompt_path.read_text()


async def _aget_last_user_message(conversation: Conversation) -> str:
    """
    Get the last user message from a conversation.

    Args:
        conversation: The Conversation model instance.

    Returns:
        The content of the last user message.

    Raises:
        ValueError: If no user messages found.
    """
    last_message = await conversation.messages.filter(role="user").order_by("-created_at").afirst()

    if not last_message:
        raise ValueError("Conversation has no user messages to process")

    return last_message.content


async def _process_conversation_async(conversation: Conversation) -> None:
    """
    Process a conversation asynchronously using the Claude Agent SDK.

    This is the async implementation that:
    1. Sets the conversation context for tools
    2. Creates a ClaudeSDKClient with our MCP tools
    3. Sends the user's message
    4. Creates messages in the database as they arrive from the SDK

    Args:
        conversation: The Conversation to process.
    """
    # Set the conversation context for tools to access
    conversation_context.set(conversation)

    # Get the last user message to send
    user_message = await _aget_last_user_message(conversation)

    # Load system prompt
    system_prompt = _load_system_prompt()

    # Configure the SDK client
    options = ClaudeAgentOptions(
        # model="us.anthropic.claude-opus-4-5-20251101-v1:0",
        model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        # model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        system_prompt=system_prompt,
        mcp_servers={"devopshero": devopshero_mcp_server},
        allowed_tools=TOOL_NAMES,
        # Accept tool usage automatically (tools handle their own side effects)
        permission_mode="bypassPermissions",
        # Pass Claude-specific AWS credentials to the subprocess
        env=get_claude_env(),
    )

    # Track pending tool calls by tool_use_id
    # Maps tool_use_id -> {name, input, start_time}
    pending_tool_calls: dict[str, dict[str, Any]] = {}

    async with ClaudeSDKClient(options=options) as client:
        # Send the user's message
        await client.query(user_message)

        # Process the response stream - create messages immediately as they arrive
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                # Extract text and tool calls from this message
                text_content = ""
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_content += block.text
                    elif isinstance(block, ToolUseBlock):
                        # Record pending tool call for tool invocations
                        pending_tool_calls[block.id] = {
                            "name": block.name,
                            "input": block.input,
                            "start_time": time.time(),
                        }

                if text_content:
                    await Message.objects.acreate(
                        conversation=conversation,
                        role=Message.Role.AGENT,
                        content_type=Message.ContentType.TEXT,
                        content=text_content,
                    )

            elif isinstance(message, UserMessage):
                # Process tool results from synthetic user messages
                if not isinstance(message.content, list):
                    logger.info("[SDK] UserMessage: content is not a list: %s", type(message.content).__name__)
                    continue

                for block in message.content:
                    if not isinstance(block, ToolResultBlock):
                        logger.info("[SDK] UserMessage: block is not a ToolResultBlock: %s", type(block).__name__)
                        continue

                    call_info = pending_tool_calls.pop(block.tool_use_id, None)
                    if not call_info:
                        logger.info("[SDK] UserMessage: call_info not found for block.tool_use_id: %s", block.tool_use_id)
                        continue

                    tool_name = call_info["name"]
                    duration_ms = int((time.time() - call_info["start_time"]) * 1000)

                    # Create TOOL_CALL message
                    await Message.objects.acreate(
                        conversation=conversation,
                        role=Message.Role.AGENT,
                        content_type=Message.ContentType.TOOL_CALL,
                        content=tool_name,
                        metadata={
                            "tool_name": tool_name,
                            "parameters": call_info["input"],
                            "result": block.content,
                            "status": "error" if block.is_error else "success",
                            "duration_ms": duration_ms,
                        },
                    )

            elif isinstance(message, ResultMessage):
                # Final result with usage/cost metadata (logged for observability)
                logger.info(
                    "[SDK] ResultMessage: turns=%s, cost=$%.4f",
                    message.num_turns,
                    message.total_cost_usd or 0,
                )

            elif isinstance(message, SystemMessage):
                # SDK-level control messages (session init, MCP status, etc.)
                logger.info("[SDK] SystemMessage: subtype=%s, data=%s", message.subtype, message.data)

            elif isinstance(message, StreamEvent):
                # Partial streaming events (when include_partial_messages=True)
                logger.info("[SDK] StreamEvent: uuid=%s, event_type=%s", message.uuid, message.event.get("type", "unknown"))

            else:
                # Catch any unexpected message types
                logger.info("[SDK] Unknown message type: %s = %s", type(message).__name__, message)

    # Update conversation timestamp
    await conversation.asave()


def process_conversation(conversation: Conversation) -> None:
    """
    Process a conversation and generate an agent response.

    This is the main entry point for the agent. It:
    1. Sets up the conversation context for MCP tools
    2. Sends the latest user message to Claude via the Agent SDK
    3. Handles tool calls automatically through the SDK
    4. Creates messages in the database as they arrive from the SDK

    Args:
        conversation: The Conversation to process.

    Raises:
        ValueError: If the conversation has no user messages.
    """
    asyncio.run(_process_conversation_async(conversation))
