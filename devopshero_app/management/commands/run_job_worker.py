"""
Management command to run the job worker.

Usage:
    python manage.py run_job_worker

The worker polls for pending jobs (deployments and environment provisioning)
and executes them. Press Ctrl+C to stop.
"""

import signal
import sys

from django.core.management.base import BaseCommand

from devopshero_app.services.deployment import job_worker


class Command(BaseCommand):
    help = "Run the job worker that processes pending deployments and environment provisioning"

    def handle(self, *args, **options):
        """Start the job worker and wait for interrupt."""
        self.stdout.write(self.style.SUCCESS("Starting job worker..."))

        # Set up signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            self.stdout.write("\n" + self.style.WARNING("Stopping job worker..."))
            job_worker.stop_worker()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start the worker
        job_worker.start_worker()

        self.stdout.write(self.style.SUCCESS(
            "Job worker running. Press Ctrl+C to stop."
        ))

        # Keep the main thread alive
        # Must loop because signal.pause() returns on ANY signal (including SIGCHLD from subprocesses)
        while job_worker.is_running():
            signal.pause()
            self.stdout.write("Signal received, continuing...")
        
        self.stdout.write(self.style.WARNING("Worker stopped"))
