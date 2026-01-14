"""
Repository analysis sub-agent.

This module provides a standalone LLM agent that analyzes repositories
and produces structured JSON findings. It operates independently of the
main deployment agent and uses standard SDK tools (Bash, Read, LS, Glob, Grep).
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

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
    TextBlock,
)
from django.conf import settings
from pydantic import ValidationError

from devopshero_app.services.agent.agent_client import get_claude_env

from .repo_analysis_schema import RepoAnalysisOutput


logger = logging.getLogger(__name__)


# Standard SDK tools for repository inspection
REPO_ANALYSIS_TOOLS = [
    "Bash",
    "Read",
    "LS",
    "Glob",
    "Grep",
]


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    return prompt_path.read_text()


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max_length, adding ellipsis if truncated."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def _log_text_delta(text: str) -> None:
    """Log a text delta event."""
    # Print text without newline to show streaming effect
    print(text, end="", flush=True)


def _log_tool_call(tool_name: str, tool_input: dict[str, Any]) -> None:
    """Log a tool call event."""
    # Format input for display
    input_str = json.dumps(tool_input, indent=2) if tool_input else "{}"
    print(f"\n[TOOL_CALL] {tool_name}")
    print(f"  Input: {_truncate(input_str, 200)}")


def _log_tool_result(tool_name: str, result: str, is_error: bool, duration_ms: int) -> None:
    """Log a tool result event."""
    status = "ERROR" if is_error else "OK"
    print(f"[TOOL_RESULT] {tool_name} ({status}, {duration_ms}ms)")
    print(f"  Result: {_truncate(result, 500)}")
    print()


def _log_complete(num_turns: int, cost_usd: float | None) -> None:
    """Log completion event."""
    cost_str = f"${cost_usd:.4f}" if cost_usd else "N/A"
    print(f"\n[COMPLETE] turns={num_turns}, cost={cost_str}")


def _extract_json_from_response(text: str) -> dict[str, Any]:
    """
    Extract JSON from the agent's response text.

    The agent is instructed to output JSON in a code block. This function
    extracts the JSON from the markdown code block.
    """
    # Try to find JSON in a code block first
    json_block_pattern = r"```(?:json)?\s*\n([\s\S]*?)\n```"
    matches = re.findall(json_block_pattern, text)

    if matches:
        # Use the last JSON block (most likely to be the final output)
        json_str = matches[-1].strip()
        return json.loads(json_str)

    # If no code block, try to parse the entire text as JSON
    # (in case the agent outputs raw JSON)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try to find JSON object pattern in the text
    json_obj_pattern = r"\{[\s\S]*\}"
    match = re.search(json_obj_pattern, text)
    if match:
        return json.loads(match.group())

    raise ValueError("Could not extract JSON from agent response")


async def analyze_repository(repo_file_url: str, verbose: bool) -> RepoAnalysisOutput:
    """
    Analyze a repository and return structured findings.

    Args:
        repo_file_url: file:// URL pointing to the local repository directory.
        verbose: If True, log all events (tool calls, reasoning, results) to stdout.

    Returns:
        RepoAnalysisOutput with structured analysis findings.

    Raises:
        ValueError: If the agent response cannot be parsed or validated.
        Exception: If the agent encounters an error.
    """
    system_prompt = _load_system_prompt()

    options = ClaudeAgentOptions(
        model=settings.CLAUDE_MODEL,
        system_prompt=system_prompt,
        allowed_tools=REPO_ANALYSIS_TOOLS,
        permission_mode="bypassPermissions",
        env=get_claude_env(),
        include_partial_messages=True,
    )

    # Track pending tool calls for timing
    pending_tool_calls: dict[str, dict[str, Any]] = {}
    accumulated_text = ""

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Analyzing repository: {repo_file_url}")
        print(f"{'=' * 60}\n")

    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"Analyze the repository at {repo_file_url}")

        async for message in client.receive_response():
            if isinstance(message, SDKStreamEvent):
                # Handle streaming text deltas
                event_type = message.event.get("type")
                if event_type == "content_block_delta":
                    delta = message.event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text_chunk = delta.get("text", "")
                        if text_chunk:
                            accumulated_text += text_chunk
                            if verbose:
                                _log_text_delta(text_chunk)

            elif isinstance(message, AssistantMessage):
                # Handle tool use blocks
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        pending_tool_calls[block.id] = {
                            "name": block.name,
                            "input": block.input,
                            "start_time": time.time(),
                        }
                        if verbose:
                            _log_tool_call(tool_name=block.name, tool_input=block.input)
                    elif isinstance(block, TextBlock):
                        # Capture final text from assistant message
                        if block.text and block.text not in accumulated_text:
                            accumulated_text += block.text

            elif isinstance(message, UserMessage):
                # Handle tool results
                if isinstance(message.content, list):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            call_info = pending_tool_calls.pop(block.tool_use_id, None)
                            if call_info and verbose:
                                duration_ms = int((time.time() - call_info["start_time"]) * 1000)
                                _log_tool_result(
                                    tool_name=call_info["name"],
                                    result=block.content,
                                    is_error=block.is_error,
                                    duration_ms=duration_ms,
                                )

            elif isinstance(message, ResultMessage):
                if verbose:
                    _log_complete(num_turns=message.num_turns, cost_usd=message.total_cost_usd)

            elif isinstance(message, SystemMessage):
                logger.debug(f"[SDK] SystemMessage: subtype={message.subtype}")

    # Extract and validate JSON from the accumulated response
    if verbose:
        print(f"\n{'=' * 60}")
        print("Extracting JSON from response...")
        print(f"{'=' * 60}\n")

    try:
        json_data = _extract_json_from_response(accumulated_text)
        result = RepoAnalysisOutput.model_validate(json_data)
        if verbose:
            print("[SUCCESS] JSON validated successfully")
            print(f"\nDescription: {result.description}")
            print(f"Language: {result.language}")
            print(f"Framework: {result.framework}")
            print(f"Service type: {result.service.type}")
        return result
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from response: {e}")
        if verbose:
            print(f"[ERROR] Failed to parse JSON: {e}")
            print(f"Response text:\n{accumulated_text[:1000]}...")
        raise ValueError(f"Failed to parse JSON from agent response: {e}") from e
    except ValidationError as e:
        logger.error(f"Failed to validate response against schema: {e}")
        if verbose:
            print(f"[ERROR] Schema validation failed: {e}")
        raise ValueError(f"Agent response failed schema validation: {e}") from e
