"""
Event types for streaming agent responses.
"""

from dataclasses import dataclass
from typing import Any
from typing import Literal


EventType = Literal[
    "start",        # Streaming started, create message container
    "text_delta",   # Text chunk to append
    "text_flush",   # Finalize current streaming text (before tool call)
    "tool_start",   # Tool execution starting
    "tool_result",  # Tool execution completed
    "complete",     # Streaming finished
    "error",        # Error occurred
]


@dataclass
class StreamEvent:
    """Event yielded during agent response streaming."""

    type: EventType
    data: Any = None
