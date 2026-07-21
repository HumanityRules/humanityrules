"""Tests for stale-job detection after a worker dies mid-job."""

from datetime import timedelta

from django.db.models import Model
from django.test import TestCase
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.services.jobs import stale_job_reaper

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
        self.repository = models.Repository.objects.create(
            organization=self.organization,
            provider="github",
            name="hermes",
            full_name="stale-reaper/hermes",
            clone_url="https://github.com/stale-reaper/hermes.git",
        )
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            repository=self.repository,
            name="Stale Agent",
            slug="stale-agent",
            app_type=models.App.AppType.WEB,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )

    def _make_stale(self, obj: Model) -> None:
        """Backdate a row past the staleness cutoff (update() bypasses auto_now)."""
        type(obj).objects.filter(id=obj.id).update(updated_at=timezone.now() - TIMEOUT - timedelta(minutes=1))

    def _create_worker_run(self, heartbeat_age: timedelta) -> models.JobWorkerRun:
        """Create a worker run whose last heartbeat was `heartbeat_age` ago."""
        return models.JobWorkerRun.objects.create(label="", heartbeat_at=timezone.now() - heartbeat_age)

    def _create_deployment(self, status: str) -> models.Deployment:
        """Create a deployment for the fixture app."""
        return models.Deployment.objects.create(
            app=self.app,
            git_ref="main",
            image_tag="stale-test",
            status=status,
        )

    def test_stale_executing_deployment_fails(self) -> None:
        deployment = self._create_deployment(status=models.Deployment.Status.DEPLOYING)
        self._make_stale(deployment)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, models.Deployment.Status.FAILED)
        self.assertIn("stale-job detection", deployment.status_message)
        self.assertIsNotNone(deployment.completed_at)

    def test_fresh_executing_deployment_is_untouched(self) -> None:
        deployment = self._create_deployment(status=models.Deployment.Status.DEPLOYING)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, models.Deployment.Status.DEPLOYING)

    def test_old_claimable_and_terminal_deployments_are_untouched(self) -> None:
        pending = self._create_deployment(status=models.Deployment.Status.PENDING)
        teardown_pending = self._create_deployment(status=models.Deployment.Status.TEARDOWN_PENDING)
        succeeded = self._create_deployment(status=models.Deployment.Status.SUCCEEDED)
        for deployment in (pending, teardown_pending, succeeded):
            self._make_stale(deployment)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        pending.refresh_from_db()
        teardown_pending.refresh_from_db()
        succeeded.refresh_from_db()
        self.assertEqual(pending.status, models.Deployment.Status.PENDING)
        self.assertEqual(teardown_pending.status, models.Deployment.Status.TEARDOWN_PENDING)
        self.assertEqual(succeeded.status, models.Deployment.Status.SUCCEEDED)

    def test_stale_tearing_down_deployment_fails(self) -> None:
        deployment = self._create_deployment(status=models.Deployment.Status.TEARING_DOWN)
        self._make_stale(deployment)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, models.Deployment.Status.FAILED)

    def test_deployment_claimed_by_dead_run_fails_fast(self) -> None:
        dead_run = self._create_worker_run(heartbeat_age=DEAD_WORKER_TIMEOUT + timedelta(minutes=1))
        deployment = self._create_deployment(status=models.Deployment.Status.DEPLOYING)
        deployment.claimed_by_run = dead_run
        deployment.save(update_fields=["claimed_by_run", "updated_at"])

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, models.Deployment.Status.FAILED)
        self.assertEqual(deployment.status_message, stale_job_reaper.DEAD_WORKER_MESSAGE)

    def test_deployment_claimed_by_live_run_is_untouched(self) -> None:
        live_run = self._create_worker_run(heartbeat_age=timedelta(seconds=10))
        deployment = self._create_deployment(status=models.Deployment.Status.DEPLOYING)
        deployment.claimed_by_run = live_run
        deployment.save(update_fields=["claimed_by_run", "updated_at"])

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, models.Deployment.Status.DEPLOYING)

    def test_deployment_claimed_by_live_run_still_reaped_on_no_progress(self) -> None:
        live_run = self._create_worker_run(heartbeat_age=timedelta(seconds=10))
        deployment = self._create_deployment(status=models.Deployment.Status.DEPLOYING)
        deployment.claimed_by_run = live_run
        deployment.save(update_fields=["claimed_by_run", "updated_at"])
        self._make_stale(deployment)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, models.Deployment.Status.FAILED)
        self.assertIn("no progress", deployment.status_message)

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

    def test_dead_worker_runs_are_pruned_and_detach_terminal_rows(self) -> None:
        ancient_run = self._create_worker_run(heartbeat_age=stale_job_reaper.WORKER_RUN_RETENTION + timedelta(hours=1))
        live_run = self._create_worker_run(heartbeat_age=timedelta(seconds=10))
        succeeded = self._create_deployment(status=models.Deployment.Status.SUCCEEDED)
        succeeded.claimed_by_run = ancient_run
        succeeded.save(update_fields=["claimed_by_run", "updated_at"])

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        succeeded.refresh_from_db()
        self.assertFalse(models.JobWorkerRun.objects.filter(id=ancient_run.id).exists())
        self.assertTrue(models.JobWorkerRun.objects.filter(id=live_run.id).exists())
        self.assertIsNone(succeeded.claimed_by_run)
        self.assertEqual(succeeded.status, models.Deployment.Status.SUCCEEDED)

    def test_claim_stamps_worker_run(self) -> None:
        from humanityrules_app.services.jobs import job_worker

        run = self._create_worker_run(heartbeat_age=timedelta(seconds=0))
        deployment = self._create_deployment(status=models.Deployment.Status.PENDING)
        original_run_id = job_worker._worker_run_id
        job_worker._worker_run_id = run.id
        try:
            claimed = job_worker._claim_pending_app_deployment(label="")
        finally:
            job_worker._worker_run_id = original_run_id

        self.assertIsNotNone(claimed)
        deployment.refresh_from_db()
        self.assertEqual(deployment.status, models.Deployment.Status.DEPLOYING)
        self.assertEqual(deployment.claimed_by_run_id, run.id)

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
        applying = models.AppPermissionRequest.objects.create(
            app=self.app,
            status=models.AppPermissionRequest.Status.APPLYING,
        )
        approved = models.AppPermissionRequest.objects.create(
            app=self.app,
            status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
        )
        for request in (applying, approved):
            self._make_stale(request)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        applying.refresh_from_db()
        approved.refresh_from_db()
        self.assertEqual(applying.status, models.AppPermissionRequest.Status.FAILED)
        self.assertEqual(approved.status, models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)

    def test_stale_running_app_removal_fails_and_reverts_app(self) -> None:
        self.app.status = models.App.Status.PENDING_REMOVAL
        self.app.save(update_fields=["status", "updated_at"])
        job = models.AppRemovalJob.objects.create(
            organization=self.organization,
            app_id_snapshot=self.app.id,
            app_slug_snapshot=self.app.slug,
            app_name_snapshot=self.app.name,
            workspace_slug_snapshot=self.workspace.slug,
            status=models.AppRemovalJob.Status.RUNNING,
        )
        self._make_stale(job)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        job.refresh_from_db()
        self.app.refresh_from_db()
        self.assertEqual(job.status, models.AppRemovalJob.Status.FAILED)
        self.assertEqual(self.app.status, models.App.Status.ACTIVE)

    def test_stale_running_cost_refresh_fails(self) -> None:
        job = models.CostRefreshJob.objects.create(
            organization=self.organization,
            app=self.app,
            status=models.CostRefreshJob.Status.RUNNING,
        )
        self._make_stale(job)

        stale_job_reaper.reap_stale_jobs(no_progress_timeout=TIMEOUT, dead_worker_timeout=DEAD_WORKER_TIMEOUT)

        job.refresh_from_db()
        self.assertEqual(job.status, models.CostRefreshJob.Status.FAILED)
