"""
Agent runner for managing background agent tasks.

This module decouples agent processing from SSE request lifecycle by running
agent tasks in the background. Key benefits:
- Client can disconnect and reconnect without losing in-progress responses
- Agent completes work and persists regardless of client connection state
- Single subscriber model with unbounded queue (safe for finite agent responses)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import UUID

from devopshero_app.models import Conversation, Message

from . import agent_service
from .agent_service import AgentStreamEvent

logger = logging.getLogger(__name__)


@dataclass
class AgentRunner:
    """State for a background agent task serving a conversation."""

    task: asyncio.Task
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)  # Unbounded
    client_connected: bool = True


# In-memory state (single instance deployment)
_runners: dict[UUID, AgentRunner] = {}
_spawn_lock = asyncio.Lock()


async def _needs_response(conversation: Conversation) -> bool:
    """Check if conversation has an unanswered user message."""
    last_message = await conversation.messages.order_by("-created_at").afirst()
    return last_message is not None and last_message.role == Message.Role.USER


async def _needs_response_by_id(conversation_id: UUID) -> bool:
    """Check if conversation needs response by ID (avoids loading full conversation)."""
    last_message = await Message.objects.filter(
        conversation_id=conversation_id
    ).order_by("-created_at").afirst()
    return last_message is not None and last_message.role == Message.Role.USER


async def _reload_conversation(conversation_id: UUID) -> Conversation:
    """Fetch fresh conversation from DB with required relations."""
    return await Conversation.objects.select_related(
        'organization', 'user'
    ).aget(id=conversation_id)


def get_runner(conversation_id: UUID) -> AgentRunner | None:
    """Get existing runner for conversation, or None if not running."""
    return _runners.get(conversation_id)


def mark_client_disconnected(conversation_id: UUID) -> None:
    """Mark the client as disconnected for a conversation's runner."""
    runner = _runners.get(conversation_id)
    if runner:
        runner.client_connected = False
        logger.info(f"Client disconnected for conversation {conversation_id}")
    else:
        logger.error(f"No runner found to mark disconnected for conversation {conversation_id}")


def mark_client_connected(conversation_id: UUID) -> None:
    """Mark the client as connected for a conversation's runner."""
    runner = _runners.get(conversation_id)
    if runner:
        runner.client_connected = True
        logger.info(f"Client reconnected for conversation {conversation_id}")
    else:
        logger.error(f"No runner found to mark connected for conversation {conversation_id}")


async def ensure_agent_running(conversation: Conversation) -> AgentRunner:
    """
    Ensure an agent runner exists for the conversation, spawning one if needed.

    Uses double-checked locking to prevent race conditions when multiple
    requests try to spawn a runner for the same conversation.

    The runner's internal loop handles checking for pending messages.
    """
    conversation_id = conversation.id

    # Fast path: runner already exists
    if conversation_id in _runners:
        logger.info(f"Reusing existing runner for conversation {conversation_id}")
        return _runners[conversation_id]

    # Slow path: acquire lock and double-check
    async with _spawn_lock:
        if conversation_id in _runners:
            return _runners[conversation_id]

        # Create runner and spawn task
        _runners[conversation_id] = AgentRunner(
            task=None,  # Set below
            event_queue=asyncio.Queue(),
            client_connected=True,
        )
        _runners[conversation_id].task = asyncio.create_task(
            _run_agent_loop(runner=_runners[conversation_id], conversation_id=conversation_id),
            name=f"agent-runner-{conversation_id}",
        )
        logger.info(f"Spawned agent runner for conversation {conversation_id}")
        return _runners[conversation_id]


async def _run_agent_loop(runner: AgentRunner, conversation_id: UUID) -> None:
    """
    Background task that polls for user messages and runs the agent.

    Exits when: client is disconnected AND no pending user message.
    Continues when: client connected (waiting for input) OR work to do.
    """
    try:
        while runner.client_connected or await _needs_response_by_id(conversation_id=conversation_id):
            conversation = await _reload_conversation(conversation_id=conversation_id)

            if await _needs_response(conversation=conversation):
                logger.info(f"Agent processing message for conversation {conversation_id}")
                async for event in agent_service.stream_response(
                    conversation=conversation,
                    fork_session=False,
                ):
                    await runner.event_queue.put(event)
                logger.info(f"Agent completed response for conversation {conversation_id}")
            else:
                # No pending message - wait before checking again
                await asyncio.sleep(0.5)

    except Exception:
        logger.exception(f"Agent runner failed for conversation {conversation_id}")
        error_event = AgentStreamEvent(type="error", data={"error": "Agent task failed unexpectedly"})
        await runner.event_queue.put(error_event)

    finally:
        # Send completion sentinel and cleanup
        await runner.event_queue.put(None)
        _runners.pop(conversation_id, None)
        logger.info(f"Agent runner exited for conversation {conversation_id}")
