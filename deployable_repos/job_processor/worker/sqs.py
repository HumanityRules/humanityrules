"""SQS operations for receiving and deleting messages."""

import json
import logging

import boto3

logger = logging.getLogger(__name__)


class SQSClient:
    """Client for SQS queue operations."""

    def __init__(self, queue_url: str, region: str, visibility_timeout: int, max_messages: int, wait_time: int):
        self._queue_url = queue_url
        self._visibility_timeout = visibility_timeout
        self._max_messages = max_messages
        self._wait_time = wait_time
        self._client = boto3.client("sqs", region_name=region)

    def receive_messages(self) -> list[dict]:
        """Receive messages from the queue using long polling."""
        response = self._client.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=self._max_messages,
            WaitTimeSeconds=self._wait_time,
            VisibilityTimeout=self._visibility_timeout,
            MessageAttributeNames=["All"],
        )

        messages = response.get("Messages", [])
        result = []

        for msg in messages:
            try:
                body = json.loads(msg["Body"])
                result.append({
                    "receipt_handle": msg["ReceiptHandle"],
                    "message_id": msg["MessageId"],
                    "body": body,
                })
            except json.JSONDecodeError:
                logger.error("Failed to parse message body as JSON: %s", msg["MessageId"])
                # Delete malformed messages to avoid infinite retries
                self.delete_message(receipt_handle=msg["ReceiptHandle"])

        return result

    def delete_message(self, receipt_handle: str) -> None:
        """Delete a message from the queue after successful processing."""
        self._client.delete_message(
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
        )
        logger.debug("Deleted message from queue")
