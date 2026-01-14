"""Cleanup task - removes old files from S3."""

import logging

from tasks import s3
from tasks.config import Config

logger = logging.getLogger(__name__)


def run(config: Config) -> bool:
    """Run the cleanup task - delete old files from S3."""
    logger.info("Starting cleanup task")

    prefix = "data/temp"
    objects = s3.list_objects(config=config, prefix=prefix)

    if not objects:
        logger.info("No objects found with prefix: %s", prefix)
        return True

    logger.info("Found %d objects to clean up", len(objects))

    deleted_count = 0
    for obj in objects:
        key = obj["Key"]
        logger.info("Processing: %s (size: %d bytes)", key, obj.get("Size", 0))

        if s3.delete_object(config=config, key=key):
            deleted_count += 1

    logger.info("Cleanup complete. Deleted %d/%d objects", deleted_count, len(objects))
    return True
