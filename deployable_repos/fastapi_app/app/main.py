import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

load_dotenv()

app = FastAPI(
    title=os.getenv("APP_NAME", "FastAPI App"),
    description="A minimal stateless FastAPI application for testing deployment patterns.",
    version="1.0.0",
)

# In-memory items storage (stateless - resets on restart)
ITEMS: dict[int, dict[str, Any]] = {
    1: {"id": 1, "name": "Item One", "description": "First sample item"},
    2: {"id": 2, "name": "Item Two", "description": "Second sample item"},
    3: {"id": 3, "name": "Item Three", "description": "Third sample item"},
}


class EchoRequest(BaseModel):
    """Request body for the echo endpoint."""

    message: str
    metadata: dict[str, Any] | None = None


class EchoResponse(BaseModel):
    """Response body for the echo endpoint."""

    echoed_message: str
    echoed_metadata: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    """Response body for the health check endpoint."""

    status: str


class EnvResponse(BaseModel):
    """Response body for the environment info endpoint."""

    app_name: str
    environment: str
    debug: bool


class Item(BaseModel):
    """Item model for the items endpoint."""

    id: int
    name: str
    description: str


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """Health check endpoint for container orchestration."""
    return HealthResponse(status="healthy")


@app.get("/env", response_model=EnvResponse, tags=["Environment"])
async def get_environment_info() -> EnvResponse:
    """Returns environment information to verify env vars work."""
    return EnvResponse(
        app_name=os.getenv("APP_NAME", "fastapi-app"),
        environment=os.getenv("ENVIRONMENT", "unknown"),
        debug=os.getenv("DEBUG", "false").lower() == "true",
    )


@app.get("/items", response_model=list[Item], tags=["Items"])
async def list_items() -> list[Item]:
    """List all available items."""
    return [Item(**item) for item in ITEMS.values()]


@app.get("/items/{item_id}", response_model=Item, tags=["Items"])
async def get_item(item_id: int) -> Item:
    """Get a specific item by ID."""
    if item_id not in ITEMS:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Item not found")
    return Item(**ITEMS[item_id])


@app.post("/echo", response_model=EchoResponse, tags=["Echo"])
async def echo(request: EchoRequest) -> EchoResponse:
    """Echo back the request body."""
    return EchoResponse(
        echoed_message=request.message,
        echoed_metadata=request.metadata,
    )
