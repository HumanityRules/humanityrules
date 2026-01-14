# Realtime Chat App

A WebSocket-based real-time chat application built with FastAPI and DynamoDB.

## Features

- WebSocket endpoint for real-time bidirectional communication
- Simple chat room with message broadcasting to all connected clients
- Connection state management in DynamoDB
- Message history persistence in DynamoDB
- In-memory fallback for local development (no DynamoDB required)

## Local Development

### Prerequisites

- Python 3.12+

### Setup

1. Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy the environment file:

```bash
cp .env.example .env
```

For local development, leave the DynamoDB table names empty to use in-memory storage.

### Running the Application

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The application runs on port **8000**.

## API Endpoints

- **GET /health** - Health check endpoint (for ALB)
- **GET /messages?limit=50** - Get recent messages (limit: 1-100)
- **WebSocket /ws** - Real-time chat connection

## WebSocket Usage

Connect to `ws://localhost:8000/ws` and send JSON messages:

```json
{
  "content": "Hello, world!",
  "sender": "username"
}
```

Received messages have the format:

```json
{
  "type": "message",
  "data": {
    "message_id": "uuid",
    "content": "Hello, world!",
    "sender": "username",
    "timestamp": "2024-01-01T12:00:00"
  }
}
```

## Environment Variables

- **DYNAMODB_CONNECTIONS_TABLE** - DynamoDB table for connection state
- **DYNAMODB_MESSAGES_TABLE** - DynamoDB table for message history
- **AWS_REGION** - AWS region (default: us-east-1)
- **PORT** - Application port (default: 8000)

## DynamoDB Table Schemas

### Connections Table

- **Partition Key:** `connection_id` (String)
- **Attributes:** `connected_at` (String, ISO timestamp)

### Messages Table

- **Partition Key:** `message_id` (String)
- **Attributes:** `content` (String), `sender` (String), `timestamp` (String, ISO timestamp)
