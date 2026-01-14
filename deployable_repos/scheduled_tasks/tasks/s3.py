"""S3 operations for scheduled tasks."""

import json
import logging
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

from tasks.config import Config

logger = logging.getLogger(__name__)


def get_s3_client(config: Config):
    """Create S3 client for the configured region."""
    return boto3.client("s3", region_name=config.aws_region)


def upload_json(config: Config, key: str, data: dict) -> bool:
    """Upload JSON data to S3."""
    if not config.s3_bucket:
        logger.warning("S3_BUCKET not configured, skipping upload")
        logger.info("Would upload to key: %s", key)
        logger.info("Data: %s", json.dumps(data, indent=2))
        return True

    client = get_s3_client(config=config)
    try:
        client.put_object(
            Bucket=config.s3_bucket,
            Key=key,
            Body=json.dumps(data, indent=2),
            ContentType="application/json",
        )
        logger.info("Uploaded JSON to s3://%s/%s", config.s3_bucket, key)
        return True
    except ClientError as e:
        logger.error("Failed to upload to S3: %s", e)
        return False


def list_objects(config: Config, prefix: str) -> list[dict]:
    """List objects in S3 bucket with given prefix."""
    if not config.s3_bucket:
        logger.warning("S3_BUCKET not configured, returning mock data")
        return _mock_objects(prefix=prefix)

    client = get_s3_client(config=config)
    try:
        response = client.list_objects_v2(Bucket=config.s3_bucket, Prefix=prefix)
        return response.get("Contents", [])
    except ClientError as e:
        logger.error("Failed to list S3 objects: %s", e)
        return []


def delete_object(config: Config, key: str) -> bool:
    """Delete an object from S3."""
    if not config.s3_bucket:
        logger.info("[DRY RUN] Would delete: %s", key)
        return True

    client = get_s3_client(config=config)
    try:
        client.delete_object(Bucket=config.s3_bucket, Key=key)
        logger.info("Deleted s3://%s/%s", config.s3_bucket, key)
        return True
    except ClientError as e:
        logger.error("Failed to delete S3 object: %s", e)
        return False


def _mock_objects(prefix: str) -> list[dict]:
    """Return mock objects for testing without S3 bucket."""
    now = datetime.utcnow()
    return [
        {"Key": f"{prefix}/old-file-1.json", "LastModified": now, "Size": 1024},
        {"Key": f"{prefix}/old-file-2.json", "LastModified": now, "Size": 2048},
        {"Key": f"{prefix}/old-file-3.json", "LastModified": now, "Size": 512},
    ]
