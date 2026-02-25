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
    title_param = mcp_tools.get_tool_input_param_for_title(tool_name, parameters)
    if title_param:
        return f"{display_name}: "
    return display_name


@register.filter
def tool_input_param_for_title(metadata: dict) -> str | None:
    """Extract and format the main parameter from tool call metadata."""
    tool_name = metadata.get("tool_name", "")
    parameters = metadata.get("parameters", {})
    return mcp_tools.get_tool_input_param_for_title(tool_name, parameters)


@register.filter
def json_pretty(value: Any) -> str:
    """Format a value as pretty-printed JSON. Sanitizes sandbox paths for display."""
    if value is None:
        return "null"
        
    if isinstance(value, str):
        value = sanitize_paths_for_display(value)
        return value

    value = sanitize_paths_for_display(value)
    return json.dumps(value, indent=2)



# Mapping from MCP tool names to custom result templates.
# Tools not in this dict get the generic JSON dump.
TOOL_RESULT_TEMPLATES = {
    "mcp__devopshero__test_docker_build": "devopshero_app/chat/tool_results/_test_docker_build.html",
}

@register.filter
def tool_result_get_template(metadata: dict) -> str:
    """Return custom result template path for a tool, or empty string for generic rendering."""
    tool_name = metadata.get("tool_name", "")
    return TOOL_RESULT_TEMPLATES.get(tool_name, "")


@register.filter
def tool_effective_status(metadata: dict) -> str:
    """Return effective status considering both MCP is_error and business-logic success field."""
    status = metadata.get("status", "success")
    if status != "success":
        return status
    result_data = metadata.get("result", "")
    if isinstance(result_data, dict) and result_data.get("success") is False:
        return "error"
    return status


