"""Job processing logic for different job types."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from worker.s3 import S3Client

logger = logging.getLogger(__name__)


def process_job(job: dict, s3_client: S3Client | None) -> bool:
    """Process a job based on its type. Returns True if successful."""
    job_type = job.get("job_type")
    payload = job.get("payload", {})

    if job_type == "echo":
        return _process_echo(payload=payload)
    elif job_type == "transform_data":
        return _process_transform_data(payload=payload, s3_client=s3_client)
    else:
        logger.error("Unknown job type: %s", job_type)
        return False


def _process_echo(payload: dict) -> bool:
    """Echo job - logs the message payload."""
    message = payload.get("message", "<no message>")
    logger.info("ECHO: %s", message)
    return True


def _process_transform_data(payload: dict, s3_client: S3Client | None) -> bool:
    """Transform data job - reads JSON from S3, transforms, writes back."""
    if s3_client is None:
        logger.error("S3 client not configured - cannot process transform_data job")
        return False

    input_key = payload.get("input_key")
    output_key = payload.get("output_key")
    transform = payload.get("transform", "uppercase")

    if not input_key or not output_key:
        logger.error("transform_data job requires input_key and output_key")
        return False

    try:
        # Read input data
        data = s3_client.read_json(key=input_key)

        # Apply transformation
        transformed = _apply_transform(data=data, transform=transform)

        # Write output
        s3_client.write_json(key=output_key, data=transformed)

        logger.info("Transformed %s -> %s using %s", input_key, output_key, transform)
        return True
    except Exception as e:
        logger.error("Failed to process transform_data job: %s", e)
        return False


def _apply_transform(data: dict, transform: str) -> dict:
    """Apply a transformation to the data."""
    if transform == "uppercase":
        return _uppercase_strings(data=data)
    elif transform == "lowercase":
        return _lowercase_strings(data=data)
    elif transform == "passthrough":
        return data
    else:
        logger.warning("Unknown transform '%s', using passthrough", transform)
        return data


def _uppercase_strings(data: dict) -> dict:
    """Recursively uppercase all string values in a dict."""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = value.upper()
        elif isinstance(value, dict):
            result[key] = _uppercase_strings(data=value)
        elif isinstance(value, list):
            result[key] = [
                _uppercase_strings(data=item) if isinstance(item, dict)
                else item.upper() if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def _lowercase_strings(data: dict) -> dict:
    """Recursively lowercase all string values in a dict."""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = value.lower()
        elif isinstance(value, dict):
            result[key] = _lowercase_strings(data=value)
        elif isinstance(value, list):
            result[key] = [
                _lowercase_strings(data=item) if isinstance(item, dict)
                else item.lower() if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            result[key] = value
    return result
