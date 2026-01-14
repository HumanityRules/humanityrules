"""Configuration loaded from environment variables."""

import os


def get_config() -> dict:
    """Load configuration from environment variables."""
    return {
        "aws_region": os.environ.get("AWS_REGION", "us-east-1"),
        "sqs_queue_url": os.environ.get("SQS_QUEUE_URL"),
        "s3_bucket": os.environ.get("S3_BUCKET"),
        "sqs_visibility_timeout": int(os.environ.get("SQS_VISIBILITY_TIMEOUT", "30")),
        "sqs_max_messages": int(os.environ.get("SQS_MAX_MESSAGES", "10")),
        "sqs_wait_time": int(os.environ.get("SQS_WAIT_TIME", "20")),
    }


def validate_config(config: dict, local_mode: bool) -> list[str]:
    """Validate configuration and return list of errors."""
    errors = []

    if not local_mode and not config["sqs_queue_url"]:
        errors.append("SQS_QUEUE_URL is required in production mode")

    if config["sqs_max_messages"] < 1 or config["sqs_max_messages"] > 10:
        errors.append("SQS_MAX_MESSAGES must be between 1 and 10")

    if config["sqs_wait_time"] < 0 or config["sqs_wait_time"] > 20:
        errors.append("SQS_WAIT_TIME must be between 0 and 20")

    return errors
