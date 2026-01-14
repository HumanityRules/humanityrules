"""Configuration from environment variables."""

import os
import logging
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Config:
    """Application configuration loaded from environment variables."""

    slack_bot_token: str
    sqs_queue_url: str
    aws_region: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        load_dotenv()

        slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        sqs_queue_url = os.environ.get("SQS_QUEUE_URL", "")
        aws_region = os.environ.get("AWS_REGION", "us-east-1")
        log_level = os.environ.get("LOG_LEVEL", "INFO")

        return cls(
            slack_bot_token=slack_bot_token,
            sqs_queue_url=sqs_queue_url,
            aws_region=aws_region,
            log_level=log_level,
        )

    def validate(self, local_mode: bool) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if not self.slack_bot_token:
            errors.append("SLACK_BOT_TOKEN is required")

        if not local_mode and not self.sqs_queue_url:
            errors.append("SQS_QUEUE_URL is required (unless running with --local)")

        return errors

    def configure_logging(self) -> None:
        """Configure logging based on log level."""
        logging.basicConfig(
            level=getattr(logging, self.log_level.upper(), logging.INFO),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
