"""DynamoDB operations with in-memory fallback for local development."""

import uuid
from datetime import datetime
from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from app.config import settings


class StorageBackend(Protocol):
    """Protocol for storage operations."""

    def add_connection(self, connection_id: str) -> None:
        """Store a new connection."""
        ...

    def remove_connection(self, connection_id: str) -> None:
        """Remove a connection."""
        ...

    def get_connections(self) -> list[str]:
        """Get all active connection IDs."""
        ...

    def save_message(self, content: str, sender: str) -> dict:
        """Save a message and return the stored message."""
        ...

    def get_recent_messages(self, limit: int) -> list[dict]:
        """Get recent messages."""
        ...


class InMemoryStorage:
    """In-memory storage for local development without DynamoDB."""

    def __init__(self):
        self.connections: dict[str, datetime] = {}
        self.messages: list[dict] = []

    def add_connection(self, connection_id: str) -> None:
        """Store a new connection."""
        self.connections[connection_id] = datetime.utcnow()

    def remove_connection(self, connection_id: str) -> None:
        """Remove a connection."""
        self.connections.pop(connection_id, None)

    def get_connections(self) -> list[str]:
        """Get all active connection IDs."""
        return list(self.connections.keys())

    def save_message(self, content: str, sender: str) -> dict:
        """Save a message and return the stored message."""
        message = {
            "message_id": str(uuid.uuid4()),
            "content": content,
            "sender": sender,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.messages.append(message)
        return message

    def get_recent_messages(self, limit: int) -> list[dict]:
        """Get recent messages, most recent first."""
        return list(reversed(self.messages[-limit:]))


class DynamoDBStorage:
    """DynamoDB storage for production use."""

    def __init__(self, connections_table: str, messages_table: str, region: str):
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.connections_table = self.dynamodb.Table(connections_table)
        self.messages_table = self.dynamodb.Table(messages_table)

    def add_connection(self, connection_id: str) -> None:
        """Store a new connection in DynamoDB."""
        self.connections_table.put_item(
            Item={
                "connection_id": connection_id,
                "connected_at": datetime.utcnow().isoformat(),
            }
        )

    def remove_connection(self, connection_id: str) -> None:
        """Remove a connection from DynamoDB."""
        try:
            self.connections_table.delete_item(Key={"connection_id": connection_id})
        except ClientError:
            pass

    def get_connections(self) -> list[str]:
        """Get all active connection IDs from DynamoDB."""
        response = self.connections_table.scan(ProjectionExpression="connection_id")
        return [item["connection_id"] for item in response.get("Items", [])]

    def save_message(self, content: str, sender: str) -> dict:
        """Save a message to DynamoDB and return the stored message."""
        message = {
            "message_id": str(uuid.uuid4()),
            "content": content,
            "sender": sender,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.messages_table.put_item(Item=message)
        return message

    def get_recent_messages(self, limit: int) -> list[dict]:
        """Get recent messages from DynamoDB, most recent first."""
        response = self.messages_table.scan()
        items = response.get("Items", [])
        sorted_items = sorted(items, key=lambda x: x.get("timestamp", ""), reverse=True)
        return sorted_items[:limit]


def get_storage() -> StorageBackend:
    """Get the appropriate storage backend based on configuration."""
    if settings.use_dynamodb:
        return DynamoDBStorage(
            connections_table=settings.dynamodb_connections_table,
            messages_table=settings.dynamodb_messages_table,
            region=settings.aws_region,
        )
    return InMemoryStorage()


# Global storage instance
storage = get_storage()
