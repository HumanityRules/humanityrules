"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Application configuration from environment."""

    task_name: str
    s3_bucket: str
    aws_region: str
    log_level: str


def load_config(task_override: str | None) -> Config:
    """Load configuration from environment variables with optional task override."""
    return Config(
        task_name=task_override or os.environ.get("TASK_NAME", ""),
        s3_bucket=os.environ.get("S3_BUCKET", ""),
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
