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
from dataclasses import dataclass, field
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
from django.conf import settings

from .mcp_tools import (
    conversation_context,
    devopshero_mcp_server,
    TOOL_NAMES,
)


@dataclass
class StreamingContext:
    """Mutable state for streaming response processing."""

    conversation: Conversation
    pending_tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    accumulated_content: str = ""


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    return prompt_path.read_text()


async def _aget_last_user_message(conversation: Conversation) -> str:
    """Get the content of the most recent user message."""
    last_message = await conversation.messages.filter(role="user").order_by("-created_at").afirst()

    if not last_message:
        raise ValueError("Conversation has no user messages to process")

    return last_message.content


async def _persist_text_message(conversation: Conversation, content: str) -> None:
    """Persist an agent text message to the database."""
    await Message.objects.acreate(
        conversation=conversation,
        role=Message.Role.AGENT,
        content_type=Message.ContentType.TEXT,
        content=content,
    )


async def _persist_tool_call(conversation: Conversation, tool_name: str, parameters: Any, result: str, status: str, duration_ms: int) -> None:
    """Persist a tool call message to the database."""
    await Message.objects.acreate(
        conversation=conversation,
        role=Message.Role.AGENT,
        content_type=Message.ContentType.TOOL_CALL,
        content=tool_name,
        metadata={
            "tool_name": tool_name,
            "parameters": parameters,
            "result": result,
            "status": status,
            "duration_ms": duration_ms,
        },
    )


async def _persist_error(conversation: Conversation, error: Exception) -> None:
    """Persist an error message to the database."""
    await Message.objects.acreate(
        conversation=conversation,
        role=Message.Role.SYSTEM,
        content_type=Message.ContentType.ERROR,
        content=f"Agent error: {str(error)}",
        metadata={"error_type": type(error).__name__},
    )


async def _handle_sdk_stream_event(message: SDKStreamEvent, ctx: StreamingContext) -> AsyncGenerator[StreamEvent, None]:
    """Handle token-level streaming events."""
    event_type = message.event.get("type")

    if event_type == "content_block_delta":
        delta = message.event.get("delta", {})
        if delta.get("type") == "text_delta":
            text_chunk = delta.get("text", "")
            if text_chunk:
                ctx.accumulated_content += text_chunk
                yield StreamEvent(type="text_delta", data={"text": text_chunk})


async def _handle_assistant_message(message: AssistantMessage, ctx: StreamingContext) -> AsyncGenerator[StreamEvent, None]:
    """Handle assistant messages containing tool use blocks."""
    for block in message.content:
        if isinstance(block, ToolUseBlock):
            # Persist any accumulated text before tool call
            if ctx.accumulated_content:
                await _persist_text_message(conversation=ctx.conversation, content=ctx.accumulated_content)
                ctx.accumulated_content = ""
            # Always flush to release streaming element IDs before tool box is inserted
            yield StreamEvent(type="text_flush")

            # Record pending tool call
            ctx.pending_tool_calls[block.id] = {
                "name": block.name,
                "input": block.input,
                "start_time": time.time(),
            }

            yield StreamEvent(
                type="tool_start",
                data={
                    "tool_use_id": block.id,
                    "name": block.name,
                    "input": block.input,
                },
            )


async def _handle_tool_results(message: UserMessage, ctx: StreamingContext) -> AsyncGenerator[StreamEvent, None]:
    """Handle tool results from synthetic user messages."""
    if not isinstance(message.content, list):
        return

    for block in message.content:
        if not isinstance(block, ToolResultBlock):
            continue

        call_info = ctx.pending_tool_calls.pop(block.tool_use_id, None)
        if not call_info:
            continue

        tool_name = call_info["name"]
        duration_ms = int((time.time() - call_info["start_time"]) * 1000)
        status = "error" if block.is_error else "success"

        await _persist_tool_call(
            conversation=ctx.conversation,
            tool_name=tool_name,
            parameters=call_info["input"],
            result=block.content,
            status=status,
            duration_ms=duration_ms,
        )

        yield StreamEvent(
            type="tool_result",
            data={
                "tool_use_id": block.tool_use_id,
                "name": tool_name,
                "input": call_info["input"],
                "result": block.content,
                "status": status,
                "duration_ms": duration_ms,
            },
        )

    # After processing all tool results, start new streaming container
    yield StreamEvent(type="start")


def _create_agent_options(system_prompt: str) -> ClaudeAgentOptions:
    """Create SDK client options with standard configuration."""
    return ClaudeAgentOptions(
        model=settings.CLAUDE_MODEL,
        system_prompt=system_prompt,
        mcp_servers={"devopshero": devopshero_mcp_server},
        allowed_tools=TOOL_NAMES,
        permission_mode="bypassPermissions",
        env=get_claude_env(),
        include_partial_messages=True,
    )


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
    conversation_context.set(conversation)
    user_message = await _aget_last_user_message(conversation)
    options = _create_agent_options(system_prompt=_load_system_prompt())
    
    # The streaming context is used to store the accumulated content and the pending tool calls, and is passed 
    # around and mutated by the event handlers
    ctx = StreamingContext(conversation=conversation)
    
    yield StreamEvent(type="start")

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_message)

            async for message in client.receive_response():
                if isinstance(message, SDKStreamEvent):
                    async for event in _handle_sdk_stream_event(message, ctx):
                        yield event

                elif isinstance(message, AssistantMessage):
                    async for event in _handle_assistant_message(message, ctx):
                        yield event

                elif isinstance(message, UserMessage):
                    async for event in _handle_tool_results(message, ctx):
                        yield event

                elif isinstance(message, ResultMessage):
                    logger.info(f"[SDK] ResultMessage: turns={message.num_turns}, cost=${message.total_cost_usd or 0:.4f}")

                elif isinstance(message, SystemMessage):
                    logger.debug(f"[SDK] SystemMessage: subtype={message.subtype}")

        if ctx.accumulated_content:
            await _persist_text_message(conversation=conversation, content=ctx.accumulated_content)

        await conversation.asave()
        yield StreamEvent(type="complete")

    except Exception as e:
        logger.exception("Error during streaming conversation processing")
        try:
            await _persist_error(conversation=conversation, error=e)
        except Exception:
            logger.exception("Failed to save error message to database")
        yield StreamEvent(type="error", data={"error": str(e)})
