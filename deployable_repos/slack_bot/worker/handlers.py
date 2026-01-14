"""Event handlers for Slack events."""

import logging
from typing import Any

from worker.slack import SlackClient


logger = logging.getLogger(__name__)


class EventHandlers:
    """Handlers for different Slack event types."""

    def __init__(self, slack_client: SlackClient):
        """Initialize with Slack client."""
        self._slack = slack_client

    def handle_event(self, event_data: dict[str, Any]) -> bool:
        """Route event to appropriate handler. Returns True if handled."""
        event_type = event_data.get("type")

        if event_type == "event_callback":
            return self._handle_event_callback(event_data)
        elif event_type == "url_verification":
            logger.info("Received URL verification challenge (should be handled by ingress)")
            return True
        else:
            logger.warning("Unknown event type: %s", event_type)
            return False

    def _handle_event_callback(self, event_data: dict[str, Any]) -> bool:
        """Handle event_callback wrapper."""
        event = event_data.get("event", {})
        event_type = event.get("type")

        if event_type == "message":
            return self._handle_message(event)
        elif event_type == "app_mention":
            return self._handle_app_mention(event)
        else:
            logger.debug("Unhandled event type: %s", event_type)
            return True

    def _handle_message(self, event: dict[str, Any]) -> bool:
        """Handle message events - echo messages back."""
        # Skip bot messages to avoid loops
        if event.get("bot_id") or self._slack.is_bot_message(event.get("user")):
            logger.debug("Skipping bot message")
            return True

        # Skip message subtypes (edits, deletes, etc.)
        if event.get("subtype"):
            logger.debug("Skipping message subtype: %s", event.get("subtype"))
            return True

        channel = event.get("channel")
        text = event.get("text", "")
        thread_ts = event.get("thread_ts") or event.get("ts")

        if not channel or not text:
            logger.warning("Message missing channel or text")
            return False

        # Simple greeting response
        text_lower = text.lower()
        if any(greeting in text_lower for greeting in ["hello", "hi", "hey"]):
            response = "Hello! I'm a Slack bot worker. How can I help you?"
        else:
            response = f"Echo: {text}"

        return self._slack.send_message(
            channel=channel,
            text=response,
            thread_ts=thread_ts,
        )

    def _handle_app_mention(self, event: dict[str, Any]) -> bool:
        """Handle app_mention events - respond when mentioned."""
        channel = event.get("channel")
        text = event.get("text", "")
        thread_ts = event.get("thread_ts") or event.get("ts")

        if not channel:
            logger.warning("App mention missing channel")
            return False

        response = f"You mentioned me! You said: {text}"

        return self._slack.send_message(
            channel=channel,
            text=response,
            thread_ts=thread_ts,
        )
