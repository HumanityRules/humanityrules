"""SQS operations for receiving Slack events."""

import json
import logging
from typing import Any

import boto3


logger = logging.getLogger(__name__)


class SQSConsumer:
    """Consumer for SQS messages containing Slack events."""

    def __init__(self, queue_url: str, region: str):
        """Initialize with queue URL and region."""
        self._queue_url = queue_url
        self._client = boto3.client("sqs", region_name=region)

    def receive_messages(self, max_messages: int, wait_time_seconds: int) -> list[dict[str, Any]]:
        """Receive messages from the queue with long polling."""
        try:
            response = self._client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=wait_time_seconds,
                MessageAttributeNames=["All"],
            )
            messages = response.get("Messages", [])
            logger.debug("Received %d messages from SQS", len(messages))
            return messages
        except Exception as e:
            logger.error("Failed to receive messages from SQS: %s", e)
            return []

    def delete_message(self, receipt_handle: str) -> bool:
        """Delete a message from the queue after successful processing."""
        try:
            self._client.delete_message(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
            )
            logger.debug("Deleted message from SQS")
            return True
        except Exception as e:
            logger.error("Failed to delete message from SQS: %s", e)
            return False

    def parse_message_body(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Parse the message body as JSON."""
        try:
            body = message.get("Body", "{}")
            return json.loads(body)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse message body: %s", e)
            return None
