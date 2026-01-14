# File Processor Service

A file processing service that demonstrates S3 integration and event-driven patterns. Supports file uploads, downloads, listing, and simple image transformations.

## Features

- Upload files to S3 (or local storage for development)
- Download and retrieve processed files
- List uploaded files
- Image resizing transformation
- S3 event webhook endpoint for event-driven processing
- Health check endpoint

## Local Setup

### Prerequisites

- Python 3.12+
- pip

### Installation

1. Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy the environment example and configure:

```bash
cp .env.example .env
```

4. Run the service:

```bash
uvicorn app.main:app --reload --port 8000
```

The service will be available at `http://localhost:8000`.

## Configuration

See `.env.example` for available configuration options.

For local development without S3, set `USE_LOCAL_STORAGE=true` (default). Files will be stored in the `./local_storage` directory.

## API Endpoints

- **POST /upload** — Upload a file
- **GET /files** — List all uploaded files
- **GET /files/{file_id}** — Download a file
- **POST /webhook/s3** — Receive S3 event notifications
- **GET /health** — Health check

## Port

The service runs on port **8000** by default.
