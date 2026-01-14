"""File Processor Service - Main FastAPI application."""

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app import processor, s3


app = FastAPI(
    title="File Processor Service",
    description="A file processing service with S3 integration and image transformation.",
    version="1.0.0",
)


class HealthResponse(BaseModel):
    status: str
    storage_mode: str


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    location: str
    storage: str


class FileInfo(BaseModel):
    file_id: str
    filename: str
    processed: bool


class S3EventRecord(BaseModel):
    eventSource: str
    eventName: str
    s3: dict


class S3Event(BaseModel):
    Records: list[S3EventRecord]


class ProcessResponse(BaseModel):
    success: bool
    file_id: str | None = None
    original_filename: str | None = None
    processed_filename: str | None = None
    location: str | None = None
    error: str | None = None


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    from app import config

    return HealthResponse(
        status="healthy",
        storage_mode="local" if config.USE_LOCAL_STORAGE else "s3",
    )


@app.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to storage."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    file_id = s3.generate_file_id()
    result = s3.upload_file(
        file_data=file.file,
        filename=file.filename,
        file_id=file_id,
    )

    return UploadResponse(
        file_id=result["file_id"],
        filename=result["filename"],
        location=result["location"],
        storage=result["storage"],
    )


@app.get("/files", response_model=list[FileInfo])
async def list_files():
    """List all uploaded files."""
    files = s3.list_files()
    return [
        FileInfo(
            file_id=f["file_id"],
            filename=f["filename"],
            processed=f["processed"],
        )
        for f in files
    ]


@app.get("/files/{file_id}")
async def download_file(file_id: str):
    """Download a file by ID."""
    result = s3.download_file(file_id=file_id)

    if result is None:
        raise HTTPException(status_code=404, detail="File not found")

    content, filename = result

    # Determine content type based on extension
    content_type = "application/octet-stream"
    if filename.lower().endswith((".jpg", ".jpeg")):
        content_type = "image/jpeg"
    elif filename.lower().endswith(".png"):
        content_type = "image/png"
    elif filename.lower().endswith(".gif"):
        content_type = "image/gif"
    elif filename.lower().endswith(".webp"):
        content_type = "image/webp"
    elif filename.lower().endswith(".txt"):
        content_type = "text/plain"
    elif filename.lower().endswith(".pdf"):
        content_type = "application/pdf"

    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/webhook/s3", response_model=list[ProcessResponse])
async def s3_webhook(event: S3Event):
    """
    Receive S3 event notifications and process files.

    This endpoint simulates handling S3 event triggers. In production,
    this would be invoked by S3 event notifications via SNS/SQS or Lambda.
    """
    results = []

    for record in event.Records:
        if not record.eventName.startswith("ObjectCreated"):
            continue

        # Extract file_id from the S3 key
        # Expected format: uploads/{file_id}/{filename}
        key = record.s3.get("object", {}).get("key", "")
        parts = key.split("/")

        if len(parts) >= 2 and parts[0] == "uploads":
            file_id = parts[1]

            result = processor.process_file(
                file_id=file_id,
                max_width=processor.DEFAULT_MAX_WIDTH,
                max_height=processor.DEFAULT_MAX_HEIGHT,
            )
            results.append(ProcessResponse(**result))

    return results


@app.post("/process/{file_id}", response_model=ProcessResponse)
async def process_file_endpoint(
    file_id: str,
    max_width: int = processor.DEFAULT_MAX_WIDTH,
    max_height: int = processor.DEFAULT_MAX_HEIGHT,
):
    """
    Manually trigger processing for a file.

    For images, this resizes them to fit within the specified dimensions
    while maintaining aspect ratio.
    """
    result = processor.process_file(
        file_id=file_id,
        max_width=max_width,
        max_height=max_height,
    )

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    return ProcessResponse(**result)
