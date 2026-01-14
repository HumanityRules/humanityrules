"""FastAPI application with WebSocket and REST endpoints."""

from fastapi import FastAPI
from fastapi import Query
from fastapi import WebSocket
from fastapi import WebSocketDisconnect

from app.dynamodb import storage
from app.websocket import manager

app = FastAPI(
    title="Realtime Chat App",
    description="WebSocket-based real-time chat with DynamoDB storage",
    version="1.0.0",
)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint for ALB."""
    return {"status": "healthy"}


@app.get("/messages")
async def get_messages(limit: int = Query(default=50, le=100, ge=1)) -> dict:
    """Get recent messages."""
    messages = storage.get_recent_messages(limit=limit)
    return {"messages": messages}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time chat."""
    connection_id = await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.handle_message(connection_id=connection_id, data=data)
    except WebSocketDisconnect:
        manager.disconnect(connection_id)


if __name__ == "__main__":
    import uvicorn

    from app.config import settings

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
