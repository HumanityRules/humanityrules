"""
Management command to run the deployment worker.

Usage:
    python manage.py run_deployment_worker

The worker polls for pending deployments and executes them.
Press Ctrl+C to stop.
"""

import signal
import sys

from django.core.management.base import BaseCommand

from devopshero_app.services.deployment import deployment_worker


class Command(BaseCommand):
    help = "Run the deployment worker that processes pending deployments"

    def handle(self, *args, **options):
        """Start the deployment worker and wait for interrupt."""
        self.stdout.write(self.style.SUCCESS("Starting deployment worker..."))

        # Set up signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            self.stdout.write("\n" + self.style.WARNING("Stopping deployment worker..."))
            deployment_worker.stop_worker()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start the worker
        deployment_worker.start_worker()

        self.stdout.write(self.style.SUCCESS(
            "Deployment worker running. Press Ctrl+C to stop."
        ))

        # Keep the main thread alive
        try:
            signal.pause()
        except AttributeError:
            # signal.pause() not available on Windows
            import time
            while deployment_worker.is_running():
                time.sleep(1)
