"""
Management command to run the job worker.

Usage:
    python manage.py run_job_worker
    python manage.py run_job_worker --label vmendi-A

The worker polls for pending jobs (deployments, env provisioning, teardowns,
permissions applies, app removals) and executes them. Press Ctrl+C to stop.

`--label` scopes the worker to App rows whose `label` matches. The default
(empty label) is the unscoped main worker that handles all UI-created traffic
plus env-level jobs. A labelled worker only picks up app-scoped work for that
label and never touches env-level jobs — those are reserved for the unscoped
worker. Use this in worktrees to avoid colliding with the main worker.
"""

import signal
import sys

from django.core.management.base import BaseCommand

from devopshero_app.services.jobs import job_worker


class Command(BaseCommand):
    help = "Run the job worker that processes pending deployments and environment provisioning"

    def add_arguments(self, parser):
        parser.add_argument(
            "--label",
            default="",
            help=(
                "Scope this worker to Apps with App.label == <label>. "
                "Empty (default) = unscoped main worker (claims label='' rows + env jobs)."
            ),
        )

    def handle(self, *args, **options):
        """Start the job worker and wait for interrupt."""
        label = options["label"]
        scope = f"label={label!r}" if label else "unscoped"
        self.stdout.write(self.style.SUCCESS(f"Starting job worker ({scope})..."))

        # Set up signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            self.stdout.write("\n" + self.style.WARNING("Stopping job worker..."))
            job_worker.stop_worker()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start the worker
        job_worker.start_worker(label=label)

        self.stdout.write(self.style.SUCCESS(
            f"Job worker running ({scope}). Press Ctrl+C to stop."
        ))

        # Keep the main thread alive
        # Must loop because signal.pause() returns on ANY signal (including SIGCHLD from subprocesses)
        while job_worker.is_running():
            signal.pause()
            self.stdout.write("Signal received, continuing...")
        
        self.stdout.write(self.style.WARNING("Worker stopped"))
