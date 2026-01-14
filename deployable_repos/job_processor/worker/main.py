"""Main entry point for the job processor worker."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys

from worker import config
from worker.processor import process_job
from worker.s3 import S3Client
from worker.sqs import SQSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
_shutdown_requested = False


def _handle_signal(signum: int, frame) -> None:
    """Handle shutdown signals gracefully."""
    global _shutdown_requested
    signal_name = signal.Signals(signum).name
    logger.info("Received %s, initiating graceful shutdown...", signal_name)
    _shutdown_requested = True


def _run_sqs_mode(sqs_client: SQSClient, s3_client: S3Client | None) -> None:
    """Run the main loop polling SQS for messages."""
    logger.info("Starting SQS polling loop")

    while not _shutdown_requested:
        try:
            messages = sqs_client.receive_messages()

            if not messages:
                logger.debug("No messages received, continuing to poll")
                continue

            logger.info("Received %d messages", len(messages))

            for msg in messages:
                if _shutdown_requested:
                    logger.info("Shutdown requested, stopping message processing")
                    break

                message_id = msg["message_id"]
                logger.info("Processing message %s", message_id)

                success = process_job(job=msg["body"], s3_client=s3_client)

                if success:
                    sqs_client.delete_message(receipt_handle=msg["receipt_handle"])
                    logger.info("Successfully processed message %s", message_id)
                else:
                    logger.error("Failed to process message %s, leaving in queue", message_id)

        except Exception as e:
            logger.error("Error in main loop: %s", e)
            if _shutdown_requested:
                break

    logger.info("Worker shutdown complete")


def _run_local_mode(s3_client: S3Client | None, file_path: str | None) -> None:
    """Run in local mode, reading jobs from stdin or a file."""
    logger.info("Running in local mode")

    if file_path:
        logger.info("Reading jobs from file: %s", file_path)
        with open(file_path, "r") as f:
            lines = f.readlines()
    else:
        logger.info("Reading jobs from stdin (one JSON per line)")
        lines = sys.stdin.readlines()

    for line_num, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue

        try:
            job = json.loads(line)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse line %d: %s", line_num, e)
            continue

        logger.info("Processing job from line %d", line_num)
        success = process_job(job=job, s3_client=s3_client)

        if success:
            logger.info("Successfully processed job from line %d", line_num)
        else:
            logger.error("Failed to process job from line %d", line_num)

    logger.info("Local mode processing complete")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Job processor worker")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run in local mode (read jobs from stdin or file instead of SQS)",
    )
    parser.add_argument(
        "--file",
        type=str,
        help="File to read jobs from in local mode (JSONL format)",
    )
    args = parser.parse_args()

    # Load and validate configuration
    cfg = config.get_config()
    errors = config.validate_config(config=cfg, local_mode=args.local)

    if errors:
        for error in errors:
            logger.error("Configuration error: %s", error)
        sys.exit(1)

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Initialize S3 client if bucket is configured
    s3_client = None
    if cfg["s3_bucket"]:
        s3_client = S3Client(bucket=cfg["s3_bucket"], region=cfg["aws_region"])
        logger.info("S3 client initialized for bucket: %s", cfg["s3_bucket"])
    else:
        logger.warning("S3_BUCKET not configured, transform_data jobs will fail")

    if args.local:
        _run_local_mode(s3_client=s3_client, file_path=args.file)
    else:
        # Initialize SQS client
        sqs_client = SQSClient(
            queue_url=cfg["sqs_queue_url"],
            region=cfg["aws_region"],
            visibility_timeout=cfg["sqs_visibility_timeout"],
            max_messages=cfg["sqs_max_messages"],
            wait_time=cfg["sqs_wait_time"],
        )
        logger.info("SQS client initialized for queue: %s", cfg["sqs_queue_url"])

        _run_sqs_mode(sqs_client=sqs_client, s3_client=s3_client)


if __name__ == "__main__":
    main()
