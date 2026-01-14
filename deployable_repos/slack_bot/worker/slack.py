"""Slack API operations."""

import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


logger = logging.getLogger(__name__)


class SlackClient:
    """Wrapper around Slack WebClient for bot operations."""

    def __init__(self, token: str):
        """Initialize with bot token."""
        self._client = WebClient(token=token)
        self._bot_user_id: str | None = None

    def get_bot_user_id(self) -> str | None:
        """Get the bot's user ID for filtering self-messages."""
        if self._bot_user_id is None:
            try:
                response = self._client.auth_test()
                self._bot_user_id = response.get("user_id")
                logger.info("Bot user ID: %s", self._bot_user_id)
            except SlackApiError as e:
                logger.error("Failed to get bot user ID: %s", e)
        return self._bot_user_id

    def send_message(self, channel: str, text: str, thread_ts: str | None) -> bool:
        """Send a message to a channel, optionally in a thread."""
        try:
            self._client.chat_postMessage(
                channel=channel,
                text=text,
                thread_ts=thread_ts,
            )
            logger.info("Sent message to channel %s", channel)
            return True
        except SlackApiError as e:
            logger.error("Failed to send message: %s", e)
            return False

    def is_bot_message(self, user_id: str | None) -> bool:
        """Check if a message is from the bot itself."""
        if user_id is None:
            return False
        bot_id = self.get_bot_user_id()
        return bot_id is not None and user_id == bot_id
