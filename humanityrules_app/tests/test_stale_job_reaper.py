"""Tests for stale-job detection after a worker dies or hangs mid-job."""

import uuid
from datetime import timedelta

from django.db.models import Model
from django.test import TestCase
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.services.jobs import stale_job_reaper
from humanityrules_app.tests.app_test_factories import make_source_template

TIMEOUT = timedelta(minutes=30)
DEAD_WORKER_TIMEOUT = timedelta(minutes=2)


class TestStaleJobReaper(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Stale Reaper", slug="stale-reaper")
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Stale AWS",
            status=models.AWSAccount.Status.CONNECTED,
        )
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Production",
            slug="production",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
        )
        self.workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Operations",
            slug="operations",
        )

    def _make_app(self, slug: str, job_status: str) -> models.App:
        """Create an app fixed in `job_status` with an open attempt id."""
        return models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            source_template=make_source_template(),
            name=slug,
            slug=slug,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
            job_status=job_status,
            last_attempt_id=uuid.uuid7(),
        )

    def _make_stale(self, obj: Model) -> None:
        """Backdate a row past the staleness cutoff (update() bypasses auto_now)."""
        type(obj).objects.filter(id=obj.id).update(updated_at=timezone.now() - TIMEOUT - timedelta(minutes=1))

    def _create_worker_run(self, heartbeat_age: timedelta) -> models.JobWorkerRun:
        """Create a worker run whose last heartbeat was `heartbeat_age` ago."""
        return models.JobWorkerRun.objects.create(label="", heartbeat_at=timezone.now() - heartbeat_age)

    def test_stale_executing_deploy_fails(self) -> None:
        app = self._make_app(slug="deployapp", job_status=models.App.JobStatus.DEPLOYING)
        self._make_stale(app)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        app.refresh_from_db()
        self.assertEqual(app.job_status, models.App.JobStatus.IDLE)
        self.assertIn("stale-job detection", app.last_attempt_error)
        self.assertTrue(
            models.DeploymentRecord.objects.filter(
                app=app,
                attempt_id=app.last_attempt_id,
                event_type=models.DeploymentRecord.EventType.DEPLOY_FAILED,
            ).exists()
        )

    def test_fresh_executing_deploy_is_untouched(self) -> None:
        app = self._make_app(slug="deployapp", job_status=models.App.JobStatus.DEPLOYING)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        app.refresh_from_db()
        self.assertEqual(app.job_status, models.App.JobStatus.DEPLOYING)

    def test_old_claimable_jobs_are_untouched(self) -> None:
        deploy_pending = self._make_app(slug="deploypending", job_status=models.App.JobStatus.DEPLOY_PENDING)
        teardown_pending = self._make_app(slug="teardownpending", job_status=models.App.JobStatus.TEARDOWN_PENDING)
        removal_pending = self._make_app(slug="removalpending", job_status=models.App.JobStatus.REMOVAL_PENDING)
        for app in (deploy_pending, teardown_pending, removal_pending):
            self._make_stale(app)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        deploy_pending.refresh_from_db()
        teardown_pending.refresh_from_db()
        removal_pending.refresh_from_db()
        self.assertEqual(deploy_pending.job_status, models.App.JobStatus.DEPLOY_PENDING)
        self.assertEqual(teardown_pending.job_status, models.App.JobStatus.TEARDOWN_PENDING)
        self.assertEqual(removal_pending.job_status, models.App.JobStatus.REMOVAL_PENDING)

    def test_stale_tearing_down_app_fails_with_teardown_event(self) -> None:
        app = self._make_app(slug="teardownapp", job_status=models.App.JobStatus.TEARING_DOWN)
        self._make_stale(app)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        app.refresh_from_db()
        self.assertEqual(app.job_status, models.App.JobStatus.IDLE)
        self.assertTrue(
            models.DeploymentRecord.objects.filter(
                app=app,
                attempt_id=app.last_attempt_id,
                event_type=models.DeploymentRecord.EventType.TEARDOWN_FAILED,
            ).exists()
        )

    def test_stale_removing_app_fails_and_stays_retryable(self) -> None:
        app = self._make_app(slug="removingapp", job_status=models.App.JobStatus.REMOVING)
        self._make_stale(app)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        app.refresh_from_db()
        # Back to idle (not wedged), so the removal can be re-queued.
        self.assertEqual(app.job_status, models.App.JobStatus.IDLE)
        self.assertTrue(
            models.DeploymentRecord.objects.filter(
                app=app,
                attempt_id=app.last_attempt_id,
                event_type=models.DeploymentRecord.EventType.REMOVAL_FAILED,
            ).exists()
        )

    def test_app_claimed_by_dead_run_fails_fast(self) -> None:
        dead_run = self._create_worker_run(heartbeat_age=DEAD_WORKER_TIMEOUT + timedelta(minutes=1))
        app = self._make_app(slug="deployapp", job_status=models.App.JobStatus.DEPLOYING)
        app.claimed_by_run = dead_run
        app.save(update_fields=["claimed_by_run", "updated_at"])

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        app.refresh_from_db()
        self.assertEqual(app.job_status, models.App.JobStatus.IDLE)
        self.assertEqual(app.last_attempt_error, stale_job_reaper.DEAD_WORKER_MESSAGE)

    def test_app_claimed_by_live_run_is_untouched(self) -> None:
        live_run = self._create_worker_run(heartbeat_age=timedelta(seconds=10))
        app = self._make_app(slug="deployapp", job_status=models.App.JobStatus.DEPLOYING)
        app.claimed_by_run = live_run
        app.save(update_fields=["claimed_by_run", "updated_at"])

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        app.refresh_from_db()
        self.assertEqual(app.job_status, models.App.JobStatus.DEPLOYING)

    def test_app_claimed_by_live_run_still_reaped_on_no_progress(self) -> None:
        live_run = self._create_worker_run(heartbeat_age=timedelta(seconds=10))
        app = self._make_app(slug="deployapp", job_status=models.App.JobStatus.DEPLOYING)
        app.claimed_by_run = live_run
        app.save(update_fields=["claimed_by_run", "updated_at"])
        self._make_stale(app)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        app.refresh_from_db()
        self.assertEqual(app.job_status, models.App.JobStatus.IDLE)
        self.assertIn("no progress", app.last_attempt_error)

    def test_environment_claimed_by_dead_run_errors_fast(self) -> None:
        dead_run = self._create_worker_run(heartbeat_age=DEAD_WORKER_TIMEOUT + timedelta(minutes=1))
        provisioning = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Provisioning",
            slug="provisioning",
            aws_region="us-east-1",
            status=models.Environment.Status.PROVISIONING,
            claimed_by_run=dead_run,
        )

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        provisioning.refresh_from_db()
        self.assertEqual(provisioning.status, models.Environment.Status.ERROR)
        self.assertEqual(provisioning.status_message, stale_job_reaper.DEAD_WORKER_MESSAGE)

    def test_dead_worker_runs_are_pruned_and_detach_idle_apps(self) -> None:
        ancient_run = self._create_worker_run(heartbeat_age=stale_job_reaper.WORKER_RUN_RETENTION + timedelta(hours=1))
        live_run = self._create_worker_run(heartbeat_age=timedelta(seconds=10))
        idle = self._make_app(slug="idleapp", job_status=models.App.JobStatus.IDLE)
        idle.claimed_by_run = ancient_run
        idle.save(update_fields=["claimed_by_run", "updated_at"])

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        idle.refresh_from_db()
        self.assertFalse(models.JobWorkerRun.objects.filter(id=ancient_run.id).exists())
        self.assertTrue(models.JobWorkerRun.objects.filter(id=live_run.id).exists())
        self.assertIsNone(idle.claimed_by_run)
        self.assertEqual(idle.job_status, models.App.JobStatus.IDLE)

    def test_claim_stamps_worker_run(self) -> None:
        from humanityrules_app.services.jobs import job_worker

        run = self._create_worker_run(heartbeat_age=timedelta(seconds=0))
        app = self._make_app(slug="deployapp", job_status=models.App.JobStatus.DEPLOY_PENDING)
        original_run_id = job_worker._worker_run_id
        job_worker._worker_run_id = run.id
        try:
            claimed = job_worker._claim_pending_app_deployment(label="")
        finally:
            job_worker._worker_run_id = original_run_id

        self.assertIsNotNone(claimed)
        app.refresh_from_db()
        self.assertEqual(app.job_status, models.App.JobStatus.DEPLOYING)
        self.assertEqual(app.claimed_by_run_id, run.id)

    def test_stale_provisioning_and_tearing_down_environments_error(self) -> None:
        provisioning = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Provisioning",
            slug="provisioning",
            aws_region="us-east-1",
            status=models.Environment.Status.PROVISIONING,
        )
        tearing_down = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Tearing Down",
            slug="tearing-down",
            aws_region="us-east-1",
            status=models.Environment.Status.TEARING_DOWN,
        )
        pending = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Pending",
            slug="pending",
            aws_region="us-east-1",
            status=models.Environment.Status.PENDING,
        )
        for environment in (provisioning, tearing_down, pending):
            self._make_stale(environment)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        provisioning.refresh_from_db()
        tearing_down.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(provisioning.status, models.Environment.Status.ERROR)
        self.assertEqual(tearing_down.status, models.Environment.Status.ERROR)
        self.assertEqual(pending.status, models.Environment.Status.PENDING)

    def test_stale_applying_permission_request_fails(self) -> None:
        app = self._make_app(slug="permapp", job_status=models.App.JobStatus.IDLE)
        applying = models.AppPermissionRequest.objects.create(
            app=app,
            status=models.AppPermissionRequest.Status.APPLYING,
        )
        approved = models.AppPermissionRequest.objects.create(
            app=app,
            status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
        )
        for request in (applying, approved):
            self._make_stale(request)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        applying.refresh_from_db()
        approved.refresh_from_db()
        self.assertEqual(applying.status, models.AppPermissionRequest.Status.FAILED)
        self.assertEqual(approved.status, models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)

    def test_stale_running_cost_refresh_fails(self) -> None:
        app = self._make_app(slug="costapp", job_status=models.App.JobStatus.IDLE)
        job = models.CostRefreshJob.objects.create(
            organization=self.organization,
            app=app,
            status=models.CostRefreshJob.Status.RUNNING,
        )
        self._make_stale(job)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        job.refresh_from_db()
        self.assertEqual(job.status, models.CostRefreshJob.Status.FAILED)
