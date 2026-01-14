"""Heartbeat task - simple health check logging."""

import logging
import platform
import socket
from datetime import datetime

from tasks.config import Config

logger = logging.getLogger(__name__)


def run(config: Config) -> bool:
    """Run the heartbeat task - log a health check message."""
    logger.info("Starting heartbeat task")

    now = datetime.utcnow()

    logger.info("=" * 50)
    logger.info("HEARTBEAT")
    logger.info("=" * 50)
    logger.info("Timestamp: %s", now.isoformat() + "Z")
    logger.info("Hostname: %s", _get_hostname())
    logger.info("Python: %s", platform.python_version())
    logger.info("Platform: %s", platform.platform())
    logger.info("AWS Region: %s", config.aws_region)
    logger.info("S3 Bucket: %s", config.s3_bucket or "(not configured)")
    logger.info("=" * 50)
    logger.info("Heartbeat complete - system is healthy")

    return True


def _get_hostname() -> str:
    """Get the current hostname safely."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"
