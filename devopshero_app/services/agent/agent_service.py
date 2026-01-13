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

import logging
import time
from collections.abc import AsyncGenerator
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
    ToolUseBlock,
    ToolResultBlock,
    SystemMessage,
    StreamEvent as SDKStreamEvent,
)

from devopshero_app.models import Conversation, Message
from devopshero_app.services.streaming_service import StreamEvent

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


async def stream_response(conversation: Conversation) -> AsyncGenerator[StreamEvent, None]:
    """
    Stream agent response for a conversation.

    This is the main entry point for streaming agent processing. It:
    1. Sets up the conversation context for MCP tools
    2. Sends the latest user message to Claude via the Agent SDK
    3. Yields text deltas for real-time frontend updates
    4. Handles tool calls and yields tool events
    5. Persists final messages to the database on completion

    Args:
        conversation: The Conversation to process.

    Yields:
        StreamEvent objects for each streaming event.

    Raises:
        ValueError: If the conversation has no user messages.
    """
    # Set the conversation context for tools to access
    conversation_context.set(conversation)

    # Get the last user message to send
    user_message = await _aget_last_user_message(conversation)

    # Load system prompt
    system_prompt = _load_system_prompt()

    # Configure the SDK client with streaming enabled
    options = ClaudeAgentOptions(
        # model="us.anthropic.claude-opus-4-5-20251101-v1:0",
        model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        # model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        system_prompt=system_prompt,
        mcp_servers={"devopshero": devopshero_mcp_server},
        allowed_tools=TOOL_NAMES,
        permission_mode="bypassPermissions",
        env=get_claude_env(),
        include_partial_messages=True,  # Enable token-level streaming
    )

    # Track pending tool calls by tool_use_id
    # Maps tool_use_id -> {name, input, start_time}
    pending_tool_calls: dict[str, dict[str, Any]] = {}

    # Accumulate text content for DB persistence
    accumulated_content: str = ""

    # Signal streaming start
    yield StreamEvent(type="start")

    try:
        async with ClaudeSDKClient(options=options) as client:
            # Send the user's message
            await client.query(user_message)

            # Process the response stream
            async for message in client.receive_response():
                if isinstance(message, SDKStreamEvent):
                    # Handle token-level streaming events
                    event_type = message.event.get("type")

                    if event_type == "content_block_delta":
                        delta = message.event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_chunk = delta.get("text", "")
                            if text_chunk:
                                # Accumulate for persistence and yield to frontend
                                accumulated_content += text_chunk
                                yield StreamEvent(
                                    type="text_delta",
                                    data={"text": text_chunk},
                                )

                elif isinstance(message, AssistantMessage):
                    # Full message received - handle tool use blocks
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            # First, persist any accumulated text before tool call
                            if accumulated_content:
                                await Message.objects.acreate(
                                    conversation=conversation,
                                    role=Message.Role.AGENT,
                                    content_type=Message.ContentType.TEXT,
                                    content=accumulated_content,
                                )
                                accumulated_content = ""
                                # Tell frontend to finalize current streaming text
                                yield StreamEvent(type="text_flush")

                            # Record pending tool call
                            pending_tool_calls[block.id] = {
                                "name": block.name,
                                "input": block.input,
                                "start_time": time.time(),
                            }

                            # Signal tool start to frontend
                            yield StreamEvent(
                                type="tool_start",
                                data={
                                    "tool_use_id": block.id,
                                    "name": block.name,
                                    "input": block.input,
                                },
                            )

                elif isinstance(message, UserMessage):
                    # Process tool results from synthetic user messages
                    if not isinstance(message.content, list):
                        continue

                    for block in message.content:
                        if not isinstance(block, ToolResultBlock):
                            continue

                        call_info = pending_tool_calls.pop(block.tool_use_id, None)
                        if not call_info:
                            continue

                        tool_name = call_info["name"]
                        duration_ms = int((time.time() - call_info["start_time"]) * 1000)

                        # Create TOOL_CALL message in DB
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

                        # Signal tool result to frontend
                        yield StreamEvent(
                            type="tool_result",
                            data={
                                "tool_use_id": block.tool_use_id,
                                "name": tool_name,
                                "result": block.content,
                                "status": "error" if block.is_error else "success",
                                "duration_ms": duration_ms,
                            },
                        )

                    # After processing all tool results, start new streaming container
                    # for any text that follows
                    yield StreamEvent(type="start")

                elif isinstance(message, ResultMessage):
                    logger.info(
                        "[SDK] ResultMessage: turns=%s, cost=$%.4f",
                        message.num_turns,
                        message.total_cost_usd or 0,
                    )

                elif isinstance(message, SystemMessage):
                    logger.debug("[SDK] SystemMessage: subtype=%s", message.subtype)

        # Persist any remaining accumulated text
        if accumulated_content:
            await Message.objects.acreate(
                conversation=conversation,
                role=Message.Role.AGENT,
                content_type=Message.ContentType.TEXT,
                content=accumulated_content,
            )

        # Update conversation timestamp
        await conversation.asave()

        # Signal completion
        yield StreamEvent(type="complete")

    except Exception as e:
        logger.exception("Error during streaming conversation processing")
        # Persist error message to database
        try:
            await Message.objects.acreate(
                conversation=conversation,
                role=Message.Role.SYSTEM,
                content_type=Message.ContentType.ERROR,
                content=f"Agent error: {str(e)}",
                metadata={"error_type": type(e).__name__},
            )
        except Exception:
            logger.exception("Failed to save error message to database")
        # Yield error event to frontend
        yield StreamEvent(type="error", data={"error": str(e)})
