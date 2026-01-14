"""Report generation task - creates and uploads a JSON report to S3."""

import logging
import platform
import socket
from datetime import datetime

from tasks import s3
from tasks.config import Config

logger = logging.getLogger(__name__)


def run(config: Config) -> bool:
    """Run the report task - generate and upload a status report."""
    logger.info("Starting report generation task")

    report = _generate_report(config=config)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    key = f"reports/status_{timestamp}.json"

    success = s3.upload_json(config=config, key=key, data=report)

    if success:
        logger.info("Report generated successfully")
    else:
        logger.error("Failed to upload report")

    return success


def _generate_report(config: Config) -> dict:
    """Generate a status report with system and runtime info."""
    now = datetime.utcnow()

    return {
        "report_type": "scheduled_status",
        "generated_at": now.isoformat() + "Z",
        "environment": {
            "aws_region": config.aws_region,
            "s3_bucket": config.s3_bucket or "(not configured)",
            "hostname": _get_hostname(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "metrics": {
            "tasks_available": ["cleanup", "report", "heartbeat"],
            "execution_timestamp": now.timestamp(),
        },
        "status": "healthy",
    }


def _get_hostname() -> str:
    """Get the current hostname safely."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"
