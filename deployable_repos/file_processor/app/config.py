"""Configuration from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# S3 Configuration
S3_BUCKET = os.getenv("S3_BUCKET", "my-file-processor-bucket")
S3_PROCESSED_PREFIX = os.getenv("S3_PROCESSED_PREFIX", "processed/")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Local Development
USE_LOCAL_STORAGE = os.getenv("USE_LOCAL_STORAGE", "true").lower() == "true"
LOCAL_STORAGE_PATH = Path(os.getenv("LOCAL_STORAGE_PATH", "./local_storage"))

# Ensure local storage directories exist when using local storage
if USE_LOCAL_STORAGE:
    LOCAL_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    (LOCAL_STORAGE_PATH / "uploads").mkdir(exist_ok=True)
    (LOCAL_STORAGE_PATH / "processed").mkdir(exist_ok=True)
