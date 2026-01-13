"""
Streaming service for real-time message delivery.

Provides asyncio Queue-based communication between agent processing
(running as background task) and SSE handlers for token-level streaming.
"""

import asyncio
import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Literal

logger = logging.getLogger(__name__)


# Event types for streaming
EventType = Literal[
    "start",        # Streaming started, create message container
    "text_delta",   # Text chunk to append
    "text_flush",   # Finalize current streaming text (before tool call)
    "tool_start",   # Tool execution starting
    "tool_result",  # Tool execution completed
    "complete",     # Streaming finished, persist message
    "error",        # Error occurred
]


@dataclass
class StreamEvent:
    """Event sent through the streaming queue."""

    type: EventType
    data: Any = None


@dataclass
class StreamingSession:
    """Tracks state for an active streaming session."""

    conversation_id: str
    queue: asyncio.Queue[StreamEvent] = field(default_factory=asyncio.Queue)
    accumulated_content: str = ""
    is_active: bool = True

    def append_text(self, text: str) -> None:
        """Accumulate text content for final persistence."""
        self.accumulated_content += text

    def reset_content(self) -> str:
        """Reset accumulated content and return what was accumulated."""
        content = self.accumulated_content
        self.accumulated_content = ""
        return content


# Global registry of active streaming sessions
# Key: conversation_id (str), Value: StreamingSession
_streaming_sessions: dict[str, StreamingSession] = {}


def get_session(conversation_id: str) -> StreamingSession | None:
    """Get an existing streaming session if one exists."""
    return _streaming_sessions.get(conversation_id)


def create_session(conversation_id: str) -> StreamingSession:
    """Create a new streaming session for a conversation."""
    # Clean up any existing session
    if conversation_id in _streaming_sessions:
        logger.info(
            "Creating new session while old one exists",
            extra={"conversation_id": conversation_id},
        )
        _streaming_sessions[conversation_id].is_active = False

    session = StreamingSession(conversation_id=conversation_id)
    _streaming_sessions[conversation_id] = session
    logger.debug(
        "Created streaming session",
        extra={"conversation_id": conversation_id},
    )
    return session


def remove_session(conversation_id: str) -> None:
    """Remove a streaming session when done."""
    if conversation_id in _streaming_sessions:
        _streaming_sessions[conversation_id].is_active = False
        del _streaming_sessions[conversation_id]
        logger.debug(
            "Removed streaming session",
            extra={"conversation_id": conversation_id},
        )
