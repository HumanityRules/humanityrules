"""S3 operations with local storage fallback."""

import uuid
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError

from app import config


def _get_s3_client():
    """Create and return an S3 client."""
    return boto3.client("s3", region_name=config.AWS_REGION)


def generate_file_id() -> str:
    """Generate a unique file ID."""
    return str(uuid.uuid4())


def upload_file(file_data: BinaryIO, filename: str, file_id: str) -> dict:
    """Upload a file to S3 or local storage."""
    if config.USE_LOCAL_STORAGE:
        return _upload_local(file_data=file_data, filename=filename, file_id=file_id)
    return _upload_s3(file_data=file_data, filename=filename, file_id=file_id)


def _upload_local(file_data: BinaryIO, filename: str, file_id: str) -> dict:
    """Upload a file to local storage."""
    upload_dir = config.LOCAL_STORAGE_PATH / "uploads" / file_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / filename
    file_path.write_bytes(file_data.read())

    return {
        "file_id": file_id,
        "filename": filename,
        "location": str(file_path),
        "storage": "local",
    }


def _upload_s3(file_data: BinaryIO, filename: str, file_id: str) -> dict:
    """Upload a file to S3."""
    s3_client = _get_s3_client()
    key = f"uploads/{file_id}/{filename}"

    s3_client.upload_fileobj(file_data, config.S3_BUCKET, key)

    return {
        "file_id": file_id,
        "filename": filename,
        "location": f"s3://{config.S3_BUCKET}/{key}",
        "storage": "s3",
    }


def download_file(file_id: str) -> tuple[bytes, str] | None:
    """Download a file by ID. Returns (content, filename) or None if not found."""
    if config.USE_LOCAL_STORAGE:
        return _download_local(file_id=file_id)
    return _download_s3(file_id=file_id)


def _download_local(file_id: str) -> tuple[bytes, str] | None:
    """Download a file from local storage."""
    # Check processed first, then uploads
    for subdir in ["processed", "uploads"]:
        dir_path = config.LOCAL_STORAGE_PATH / subdir / file_id
        if dir_path.exists():
            files = list(dir_path.iterdir())
            if files:
                file_path = files[0]
                return file_path.read_bytes(), file_path.name
    return None


def _download_s3(file_id: str) -> tuple[bytes, str] | None:
    """Download a file from S3."""
    s3_client = _get_s3_client()

    # Check processed first, then uploads
    for prefix in [config.S3_PROCESSED_PREFIX, "uploads/"]:
        try:
            # List objects to find the filename
            response = s3_client.list_objects_v2(
                Bucket=config.S3_BUCKET,
                Prefix=f"{prefix}{file_id}/",
            )

            if "Contents" in response and response["Contents"]:
                key = response["Contents"][0]["Key"]
                filename = Path(key).name

                obj = s3_client.get_object(Bucket=config.S3_BUCKET, Key=key)
                return obj["Body"].read(), filename
        except ClientError:
            continue

    return None


def list_files() -> list[dict]:
    """List all uploaded files."""
    if config.USE_LOCAL_STORAGE:
        return _list_local()
    return _list_s3()


def _list_local() -> list[dict]:
    """List files from local storage."""
    files = []

    uploads_dir = config.LOCAL_STORAGE_PATH / "uploads"
    processed_dir = config.LOCAL_STORAGE_PATH / "processed"

    # Track which file_ids have been processed
    processed_ids = set()
    if processed_dir.exists():
        for file_dir in processed_dir.iterdir():
            if file_dir.is_dir():
                processed_ids.add(file_dir.name)

    if uploads_dir.exists():
        for file_dir in uploads_dir.iterdir():
            if file_dir.is_dir():
                file_id = file_dir.name
                dir_files = list(file_dir.iterdir())
                if dir_files:
                    files.append({
                        "file_id": file_id,
                        "filename": dir_files[0].name,
                        "processed": file_id in processed_ids,
                    })

    return files


def _list_s3() -> list[dict]:
    """List files from S3."""
    s3_client = _get_s3_client()
    files = []

    try:
        # Get list of processed files
        processed_response = s3_client.list_objects_v2(
            Bucket=config.S3_BUCKET,
            Prefix=config.S3_PROCESSED_PREFIX,
        )
        processed_ids = set()
        if "Contents" in processed_response:
            for obj in processed_response["Contents"]:
                parts = obj["Key"].split("/")
                if len(parts) >= 2:
                    processed_ids.add(parts[1])

        # List uploads
        response = s3_client.list_objects_v2(
            Bucket=config.S3_BUCKET,
            Prefix="uploads/",
        )

        if "Contents" in response:
            seen_ids = set()
            for obj in response["Contents"]:
                parts = obj["Key"].split("/")
                if len(parts) >= 3:
                    file_id = parts[1]
                    if file_id not in seen_ids:
                        seen_ids.add(file_id)
                        files.append({
                            "file_id": file_id,
                            "filename": parts[2],
                            "processed": file_id in processed_ids,
                        })
    except ClientError:
        pass

    return files


def save_processed_file(file_id: str, filename: str, content: bytes) -> dict:
    """Save a processed file."""
    if config.USE_LOCAL_STORAGE:
        return _save_processed_local(file_id=file_id, filename=filename, content=content)
    return _save_processed_s3(file_id=file_id, filename=filename, content=content)


def _save_processed_local(file_id: str, filename: str, content: bytes) -> dict:
    """Save a processed file to local storage."""
    processed_dir = config.LOCAL_STORAGE_PATH / "processed" / file_id
    processed_dir.mkdir(parents=True, exist_ok=True)

    file_path = processed_dir / filename
    file_path.write_bytes(content)

    return {
        "file_id": file_id,
        "filename": filename,
        "location": str(file_path),
    }


def _save_processed_s3(file_id: str, filename: str, content: bytes) -> dict:
    """Save a processed file to S3."""
    s3_client = _get_s3_client()
    key = f"{config.S3_PROCESSED_PREFIX}{file_id}/{filename}"

    s3_client.put_object(
        Bucket=config.S3_BUCKET,
        Key=key,
        Body=content,
    )

    return {
        "file_id": file_id,
        "filename": filename,
        "location": f"s3://{config.S3_BUCKET}/{key}",
    }


def get_file_for_processing(file_id: str) -> tuple[bytes, str] | None:
    """Get the original uploaded file for processing."""
    if config.USE_LOCAL_STORAGE:
        dir_path = config.LOCAL_STORAGE_PATH / "uploads" / file_id
        if dir_path.exists():
            files = list(dir_path.iterdir())
            if files:
                return files[0].read_bytes(), files[0].name
        return None

    # S3 path
    s3_client = _get_s3_client()
    try:
        response = s3_client.list_objects_v2(
            Bucket=config.S3_BUCKET,
            Prefix=f"uploads/{file_id}/",
        )

        if "Contents" in response and response["Contents"]:
            key = response["Contents"][0]["Key"]
            filename = Path(key).name
            obj = s3_client.get_object(Bucket=config.S3_BUCKET, Key=key)
            return obj["Body"].read(), filename
    except ClientError:
        pass

    return None
