# FastAPI App

A minimal stateless FastAPI application for testing deployment patterns without database dependencies.

## Features

- REST API endpoints (`/items`, `/echo`)
- OpenAPI/Swagger documentation at `/docs`
- Health check endpoint at `/health`
- Environment info endpoint at `/env`

## Requirements

- Python 3.12+

## Local Setup

1. Create and activate a virtual environment:

```bash
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy the environment file and configure as needed:

```bash
cp .env.example .env
```

4. Start the development server:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The server will be available at `http://localhost:8000`.

## API Documentation

Once the server is running, access the interactive API documentation:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

## Endpoints

- **GET /health** - Health check endpoint
- **GET /env** - Returns environment information
- **GET /items** - List all items
- **GET /items/{item_id}** - Get a specific item
- **POST /echo** - Echo back the request body

## Port

This application runs on port **8000** by default.
