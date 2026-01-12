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
import json
import time
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    UserMessage,
)
from claude_agent_sdk.types import TextBlock, ToolUseBlock, ToolResultBlock

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


def _extract_deferred_choice(block_content: Any) -> dict[str, Any] | None:
    """
    Extract deferred_choice data from a tool result block.

    The ask_user MCP tool returns deferred_choice data so we can create
    the CHOICE message at the right time for correct ordering.

    Args:
        block_content: The ToolResultBlock.content (str, list, or dict).

    Returns:
        The deferred_choice dict if present, None otherwise.
    """
    try:
        # block.content format varies:
        # - String: JSON text
        # - List: MCP content blocks [{"type": "text", "text": "..."}]
        # - Dict: Already parsed JSON
        if isinstance(block_content, str):
            result_data = json.loads(block_content)
        elif isinstance(block_content, list) and block_content:
            first_block = block_content[0]
            if isinstance(first_block, dict):
                text = first_block.get("text", "")
                result_data = json.loads(text) if text else {}
            else:
                return None
        elif isinstance(block_content, dict):
            result_data = block_content
        else:
            return None

        return result_data.get("deferred_choice")
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None


def _convert_ask_user_question_to_choice(tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Convert Claude Code's AskUserQuestion tool input to CHOICE message format.

    AskUserQuestion format:
    {
        "questions": [
            {
                "question": "Which library should we use?",
                "header": "Library Selection",
                "options": [
                    {"label": "React", "description": "Popular UI library"},
                    {"label": "Vue", "description": "Progressive framework"}
                ],
                "multiSelect": false
            }
        ]
    }

    CHOICE format:
    - content: question text
    - metadata.choices: list of {id, label, primary}
    - metadata.allow_text: boolean

    Args:
        tool_input: The parameters passed to AskUserQuestion tool.

    Returns:
        Tuple of (content, metadata) for creating a CHOICE message.
    """
    questions = tool_input.get("questions", [])
    if not questions:
        return ("Please respond:", {"choices": [], "allow_text": True})

    # Handle first question (v1 limitation: only support single question)
    first_question = questions[0]
    question_text = first_question.get("question", "")
    header = first_question.get("header", "")
    options = first_question.get("options", [])
    multi_select = first_question.get("multiSelect", False)

    # Build content - include header if present and different from question
    if header and header != question_text:
        content = f"**{header}**\n\n{question_text}"
    else:
        content = question_text or "Please select an option:"

    # Convert options to choices
    choices = []
    for i, option in enumerate(options):
        label = option.get("label", f"Option {i + 1}")
        description = option.get("description", "")

        # Include description in display if present
        if description:
            display_label = f"{label} - {description}"
        else:
            display_label = label

        choices.append({
            "id": str(uuid.uuid4()),
            "label": display_label,
            "primary": i == 0,
        })

    metadata = {
        "choices": choices,
        "allow_text": True,
        "multi_select": multi_select,
        "original_format": "AskUserQuestion",
    }

    return (content, metadata)


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

    # Track response content and metadata
    response_text = ""
    total_cost_usd = None
    usage = None
    num_turns = 0
    session_id = None

    # Track pending tool calls by tool_use_id
    # Maps tool_use_id -> {name, input, start_time}
    pending_tool_calls: dict[str, dict[str, Any]] = {}

    # Collected tool messages to create before TEXT (for correct display order)
    # Order: TOOL_CALL -> CHOICE -> TEXT (model commentary comes last)
    tool_messages: list[dict[str, Any]] = []

    async with ClaudeSDKClient(options=options) as client:
        # Send the user's message
        await client.query(user_message)

        # Process the response stream
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                # Process content blocks from assistant's response
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text
                    elif isinstance(block, ToolUseBlock):
                        # Record pending tool call (tool invocation)
                        pending_tool_calls[block.id] = {
                            "name": block.name,
                            "input": block.input,
                            "start_time": time.time(),
                        }

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

                    # Claude Code's AskUserQuestion: only CHOICE, no TOOL_CALL
                    if tool_name == "AskUserQuestion":
                        content, metadata = _convert_ask_user_question_to_choice(tool_input=call_info["input"])
                        tool_messages.append({
                            "content_type": Message.ContentType.CHOICE,
                            "content": content,
                            "metadata": metadata,
                        })
                        continue

                    # All other tools get a TOOL_CALL message
                    tool_messages.append({
                        "content_type": Message.ContentType.TOOL_CALL,
                        "content": tool_name,
                        "metadata": {
                            "tool_name": tool_name,
                            "parameters": call_info["input"],
                            "result": block.content,
                            "status": "error" if block.is_error else "success",
                            "duration_ms": duration_ms,
                        },
                    })

                    # For ask_user, also add a CHOICE message
                    if tool_name == "mcp__devopshero__ask_user":
                        deferred_choice = _extract_deferred_choice(block.content)
                        if deferred_choice:
                            tool_messages.append({
                                "content_type": Message.ContentType.CHOICE,
                                "content": deferred_choice["content"],
                                "metadata": deferred_choice["metadata"],
                            })

            elif isinstance(message, ResultMessage):
                # Capture final metadata
                total_cost_usd = message.total_cost_usd
                usage = message.usage
                num_turns = message.num_turns
                session_id = message.session_id

    # Create tool messages first (TOOL_CALL, CHOICE), then TEXT last
    for msg_data in tool_messages:
        await Message.objects.acreate(
            conversation=conversation,
            role=Message.Role.AGENT,
            **msg_data,
        )

    # Save agent TEXT response last (model's commentary after tool results)
    agent_message = await Message.objects.acreate(
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
    await conversation.asave()

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
