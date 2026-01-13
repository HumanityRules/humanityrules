"""Template filters for chat message rendering."""

import json
from typing import Any

from django import template

register = template.Library()


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
    the text content within them.

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

    return json.dumps(value, indent=2)
