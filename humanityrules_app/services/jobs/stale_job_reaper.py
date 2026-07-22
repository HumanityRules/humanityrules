"""Fail job rows abandoned in an executing status by a killed worker.

Jobs run as in-process threads of the CP web process, so a CP deployment (or
crash) kills them mid-flight and leaves their rows in an executing status
forever, blocking re-claims for the same app and environment teardowns. Rows in
a claimable status (pending, teardown_pending, approved_pending_apply) are left
alone — a new worker picks them up normally.

Detection has two tiers:

1. Dead worker (fast, ~minutes): claims stamp `claimed_by_run`; a row whose
   JobWorkerRun stopped heartbeating has no live thread behind it. Safe during
   rolling CP deploys — the draining task keeps beating until it actually dies.
2. No progress (slow fallback): executors bump `updated_at` on every phase
   transition, so a row untouched for longer than the timeout is either a hung
   thread or was set to an executing status outside a worker claim (inline
   teardowns), where no lease exists.
"""

import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from humanityrules_app import models

from . import app_job_service

logger = logging.getLogger(__name__)

DEAD_WORKER_MESSAGE = "Failed by stale-job detection: owning worker died mid-job (likely a CP deployment)"
NO_PROGRESS_MESSAGE_TEMPLATE = "Failed by stale-job detection: no progress for over {minutes} minutes"

# Dead JobWorkerRun rows older than this are deleted (SET_NULL detaches any
# rows still pointing at them, moving those to the no-progress tier).
WORKER_RUN_RETENTION = timedelta(days=1)

REAPABLE_ENVIRONMENT_STATUSES = (
    models.Environment.Status.PROVISIONING,
    models.Environment.Status.TEARING_DOWN,
)


def reap_stale_jobs(no_progress_timeout: timedelta, dead_worker_timeout: timedelta) -> None:
    """Fail executing job rows whose worker run died or that stopped making progress."""
    now = timezone.now()
    # Dead-worker tier runs first so rows matching both get the specific message.
    tiers = (
        (Q(claimed_by_run__isnull=False, claimed_by_run__heartbeat_at__lt=now - dead_worker_timeout), DEAD_WORKER_MESSAGE),
        (Q(updated_at__lt=now - no_progress_timeout), NO_PROGRESS_MESSAGE_TEMPLATE.format(minutes=int(no_progress_timeout.total_seconds() // 60))),
    )
    for stale_q, message in tiers:
        _reap_stale_app_jobs(stale_q=stale_q, message=message)
        _reap_stale_environments(stale_q=stale_q, message=message)
        _reap_stale_permission_applies(stale_q=stale_q, message=message)
        _reap_stale_cost_refreshes(stale_q=stale_q, message=message)
    _prune_dead_worker_runs(cutoff=now - WORKER_RUN_RETENTION)


def _reap_stale_app_jobs(stale_q: Q, message: str) -> None:
    """Fail stale executing app jobs (deploys, teardowns, removals) back to IDLE."""
    with transaction.atomic():
        stale_apps = list(
            models.App.objects
            .select_for_update(skip_locked=True, of=("self",))
            .filter(stale_q, job_status__in=models.App.EXECUTING_JOB_STATUSES)
        )
        if not stale_apps:
            return

        for app in stale_apps:
            app_job_service.settle_failure(app=app, error=message)

    logger.error(f"Stale-job reaper failed {len(stale_apps)} app job(s): {[app.slug for app in stale_apps]}")


def _reap_stale_environments(stale_q: Q, message: str) -> None:
    """Move stale provisioning/tearing-down environments to ERROR."""
    reaped = models.Environment.objects.filter(stale_q, status__in=REAPABLE_ENVIRONMENT_STATUSES).update(
        status=models.Environment.Status.ERROR,
        status_message=message,
        updated_at=timezone.now(),
    )
    if reaped:
        logger.error(f"Stale-job reaper errored {reaped} environment(s)")


def _reap_stale_permission_applies(stale_q: Q, message: str) -> None:
    """Fail stale applying permission requests."""
    reaped = models.AppPermissionRequest.objects.filter(stale_q, status=models.AppPermissionRequest.Status.APPLYING).update(
        status=models.AppPermissionRequest.Status.FAILED,
        status_message=message,
        updated_at=timezone.now(),
    )
    if reaped:
        logger.error(f"Stale-job reaper failed {reaped} permission apply(ies)")


def _reap_stale_cost_refreshes(stale_q: Q, message: str) -> None:
    """Fail stale running cost refresh jobs."""
    reaped = models.CostRefreshJob.objects.filter(stale_q, status=models.CostRefreshJob.Status.RUNNING).update(
        status=models.CostRefreshJob.Status.FAILED,
        status_message=message,
        updated_at=timezone.now(),
    )
    if reaped:
        logger.error(f"Stale-job reaper failed {reaped} cost refresh(es)")


def _prune_dead_worker_runs(cutoff: datetime) -> None:
    """Delete worker-run rows long past their last heartbeat."""
    deleted, _ = models.JobWorkerRun.objects.filter(heartbeat_at__lt=cutoff).delete()
    if deleted:
        logger.info(f"Stale-job reaper pruned {deleted} dead worker run(s)")
