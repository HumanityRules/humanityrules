"""WebSocket connection handling and message broadcasting."""

import json
import uuid
from dataclasses import dataclass
from dataclasses import field

from fastapi import WebSocket

from app.dynamodb import storage


@dataclass
class ConnectionManager:
    """Manages active WebSocket connections and message broadcasting."""

    active_connections: dict[str, WebSocket] = field(default_factory=dict)

    async def connect(self, websocket: WebSocket) -> str:
        """Accept a WebSocket connection and return its ID."""
        await websocket.accept()
        connection_id = str(uuid.uuid4())
        self.active_connections[connection_id] = websocket
        storage.add_connection(connection_id)
        return connection_id

    def disconnect(self, connection_id: str) -> None:
        """Remove a WebSocket connection."""
        self.active_connections.pop(connection_id, None)
        storage.remove_connection(connection_id)

    async def broadcast(self, message: dict) -> None:
        """Broadcast a message to all connected clients."""
        message_json = json.dumps(message)
        disconnected = []
        for connection_id, websocket in self.active_connections.items():
            try:
                await websocket.send_text(message_json)
            except Exception:
                disconnected.append(connection_id)
        for connection_id in disconnected:
            self.disconnect(connection_id)

    async def handle_message(self, connection_id: str, data: str) -> None:
        """Handle an incoming message from a client."""
        try:
            payload = json.loads(data)
            content = payload.get("content", "")
            sender = payload.get("sender", "anonymous")
        except json.JSONDecodeError:
            content = data
            sender = "anonymous"

        if not content:
            return

        message = storage.save_message(content=content, sender=sender)
        await self.broadcast(
            {
                "type": "message",
                "data": message,
            }
        )


manager = ConnectionManager()
