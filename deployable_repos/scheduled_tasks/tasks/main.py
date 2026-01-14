"""Entry point for scheduled tasks - selects and runs the appropriate task."""

import argparse
import logging
import sys
from typing import Callable

from dotenv import load_dotenv

from tasks import cleanup, heartbeat, report
from tasks.config import Config, load_config

# Task registry mapping task names to their run functions
TASKS: dict[str, Callable[[Config], bool]] = {
    "cleanup": cleanup.run,
    "heartbeat": heartbeat.run,
    "report": report.run,
}


def setup_logging(level: str) -> None:
    """Configure logging for the task runner."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run scheduled tasks triggered by EventBridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available tasks:
  cleanup    - Clean up old files in S3
  report     - Generate and upload a status report to S3
  heartbeat  - Log a heartbeat message

Examples:
  python -m tasks.main --task=cleanup
  python -m tasks.main --task=report
  TASK_NAME=heartbeat python -m tasks.main
        """,
    )
    parser.add_argument(
        "--task",
        type=str,
        help="Task to run (overrides TASK_NAME env var)",
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point - load config, select task, and run."""
    load_dotenv()

    args = parse_args()
    config = load_config(task_override=args.task)

    setup_logging(level=config.log_level)
    logger = logging.getLogger(__name__)

    if not config.task_name:
        logger.error("No task specified. Use --task=<name> or set TASK_NAME env var")
        logger.error("Available tasks: %s", ", ".join(TASKS.keys()))
        return 1

    if config.task_name not in TASKS:
        logger.error("Unknown task: %s", config.task_name)
        logger.error("Available tasks: %s", ", ".join(TASKS.keys()))
        return 1

    logger.info("Running task: %s", config.task_name)

    try:
        task_fn = TASKS[config.task_name]
        success = task_fn(config)

        if success:
            logger.info("Task '%s' completed successfully", config.task_name)
            return 0
        else:
            logger.error("Task '%s' failed", config.task_name)
            return 1

    except Exception as e:
        logger.exception("Task '%s' raised an exception: %s", config.task_name, e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
