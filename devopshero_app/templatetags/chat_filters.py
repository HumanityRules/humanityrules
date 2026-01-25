"""Template filters for chat message rendering."""

import json
from typing import Any

from django import template

from devopshero_app.services.agent import mcp_tools
from devopshero_app.services.agent.mcp_tools import sanitize_paths_for_display

register = template.Library()


@register.filter
def tool_display_name(metadata: dict) -> str:
    """Get display name with colon suffix if main param exists."""
    tool_name = metadata.get("tool_name", "")
    parameters = metadata.get("parameters", {})
    display_name = mcp_tools.get_tool_display_name(tool_name, parameters)
    main_param = mcp_tools.get_tool_main_param(tool_name, parameters)
    if main_param:
        return f"{display_name}: "
    return display_name


@register.filter
def tool_main_param(metadata: dict) -> str | None:
    """Extract and format the main parameter from tool call metadata."""
    tool_name = metadata.get("tool_name", "")
    parameters = metadata.get("parameters", {})
    return mcp_tools.get_tool_main_param(tool_name, parameters)


def extract_mcp_text_content(value: Any) -> Any:
    """Extract text content from MCP content block structure.

    MCP tool results come as: [{"type": "text", "text": "..."}]
    This extracts the text and tries to parse it as JSON.
    """
    if not isinstance(value, list):
        return value

    # Check if it's an MCP content block list
    if len(value) == 0:
        return value

    # Extract text from all text blocks
    texts = []
    for block in value:
        if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
            texts.append(block["text"])

    if not texts:
        return value

    # Join all text blocks
    combined_text = "\n".join(texts)

    # Try to parse as JSON
    try:
        return json.loads(combined_text)
    except json.JSONDecodeError:
        # Return as plain text if not valid JSON
        return combined_text


@register.filter
def json_pretty(value: Any) -> str:
    """Format a dict/list or JSON string as pretty-printed JSON.

    Handles MCP content block structures by extracting and parsing
    the text content within them. Sanitizes sandbox paths for display.

    Args:
        value: A dict, list, JSON string, or MCP content block.

    Returns:
        Pretty-printed JSON string with 2-space indentation.
    """
    if value is None:
        return "null"

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value

    # Try to extract MCP text content
    value = extract_mcp_text_content(value)

    # If it's now a string (extracted from MCP), return as-is
    if isinstance(value, str):
        return value

    # Sanitize sandbox paths for display
    value = sanitize_paths_for_display(value)

    return json.dumps(value, indent=2)
