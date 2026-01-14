"""Entry point with main loop for the Slack bot worker."""

import argparse
import json
import logging
import signal
import sys
from typing import Any

from worker.config import Config
from worker.slack import SlackClient
from worker.sqs import SQSConsumer
from worker.handlers import EventHandlers


logger = logging.getLogger(__name__)


class Worker:
    """Main worker that processes Slack events from SQS."""

    def __init__(self, config: Config, local_mode: bool):
        """Initialize worker with configuration."""
        self._config = config
        self._local_mode = local_mode
        self._running = True
        self._slack = SlackClient(token=config.slack_bot_token)
        self._handlers = EventHandlers(slack_client=self._slack)

        if not local_mode:
            self._sqs = SQSConsumer(
                queue_url=config.sqs_queue_url,
                region=config.aws_region,
            )
        else:
            self._sqs = None

    def run(self) -> None:
        """Main loop - poll SQS and process events."""
        logger.info("Starting Slack bot worker...")

        if self._local_mode:
            self._run_local_mode()
        else:
            self._run_sqs_mode()

        logger.info("Worker stopped")

    def _run_sqs_mode(self) -> None:
        """Run in production mode, polling SQS."""
        logger.info("Running in SQS mode, polling queue: %s", self._config.sqs_queue_url)

        while self._running:
            messages = self._sqs.receive_messages(
                max_messages=10,
                wait_time_seconds=20,
            )

            for message in messages:
                if not self._running:
                    break

                self._process_sqs_message(message)

    def _run_local_mode(self) -> None:
        """Run in local mode for testing without SQS."""
        logger.info("Running in local mode (interactive)")
        print("\n" + "=" * 50)
        print("Local mode: Enter JSON events or simple text messages")
        print("Type 'quit' to exit")
        print("=" * 50 + "\n")

        while self._running:
            try:
                user_input = input("Event> ").strip()

                if not user_input:
                    continue

                if user_input.lower() == "quit":
                    break

                event = self._parse_local_input(user_input)
                if event:
                    self._handlers.handle_event(event)

            except EOFError:
                break
            except KeyboardInterrupt:
                break

    def _parse_local_input(self, user_input: str) -> dict[str, Any] | None:
        """Parse local input as JSON or create a simulated message event."""
        # Try to parse as JSON first
        if user_input.startswith("{"):
            try:
                return json.loads(user_input)
            except json.JSONDecodeError:
                logger.error("Invalid JSON input")
                return None

        # Create a simulated message event
        return {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C_LOCAL_TEST",
                "user": "U_LOCAL_USER",
                "text": user_input,
                "ts": "1234567890.123456",
            },
        }

    def _process_sqs_message(self, message: dict[str, Any]) -> None:
        """Process a single SQS message."""
        receipt_handle = message.get("ReceiptHandle")
        event_data = self._sqs.parse_message_body(message)

        if event_data is None:
            logger.warning("Skipping message with invalid body")
            if receipt_handle:
                self._sqs.delete_message(receipt_handle)
            return

        try:
            success = self._handlers.handle_event(event_data)
            if success and receipt_handle:
                self._sqs.delete_message(receipt_handle)
        except Exception as e:
            logger.error("Error processing event: %s", e)

    def shutdown(self) -> None:
        """Signal the worker to stop."""
        logger.info("Shutdown requested, finishing current work...")
        self._running = False


def setup_signal_handlers(worker: Worker) -> None:
    """Set up signal handlers for graceful shutdown."""

    def handle_signal(signum: int, frame: Any) -> None:
        signal_name = signal.Signals(signum).name
        logger.info("Received %s signal", signal_name)
        worker.shutdown()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Slack bot worker")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run in local mode without SQS (interactive testing)",
    )
    args = parser.parse_args()

    config = Config.from_env()
    config.configure_logging()

    errors = config.validate(local_mode=args.local)
    if errors:
        for error in errors:
            logger.error("Configuration error: %s", error)
        sys.exit(1)

    worker = Worker(config=config, local_mode=args.local)
    setup_signal_handlers(worker)

    try:
        worker.run()
    except Exception as e:
        logger.exception("Worker failed with error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
