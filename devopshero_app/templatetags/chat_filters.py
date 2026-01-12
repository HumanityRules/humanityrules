"""Template filters for chat message rendering."""

import json
from typing import Any

from django import template

register = template.Library()


@register.filter
def json_pretty(value: Any) -> str:
    """Format a dict/list or JSON string as pretty-printed JSON.

    Args:
        value: A dict, list, or JSON string to format.

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

    return json.dumps(value, indent=2)
