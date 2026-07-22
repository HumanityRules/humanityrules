"""Tests for process-local CDK job concurrency limiting."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import SimpleTestCase

from humanityrules_app.services.jobs import job_worker


class CdkJobConcurrencyTests(SimpleTestCase):
    def setUp(self) -> None:
        self.original_cdk_job_slots = job_worker._cdk_job_slots
        job_worker._cdk_job_slots = threading.BoundedSemaphore(value=2)

    def tearDown(self) -> None:
        job_worker._cdk_job_slots = self.original_cdk_job_slots

    def _job(self) -> SimpleNamespace:
        """Create the minimal shape needed by the worker's thread launcher."""
        app_id = uuid4()
        return SimpleNamespace(id=app_id, slug=f"app-{app_id.hex[:8]}")

    def _run_started_thread(self, thread_class: MagicMock, call_index: int) -> None:
        """Invoke a captured thread target synchronously."""
        thread_call = thread_class.call_args_list[call_index]
        thread_call.kwargs["target"](*thread_call.kwargs["args"])

    def test_third_app_deployment_remains_unclaimed_until_slot_is_released(self) -> None:
        jobs = [self._job(), self._job(), self._job()]

        with (
            patch.object(job_worker, "_claim_pending_app_deployment", side_effect=jobs) as claim,
            patch.object(job_worker, "_run_app_deployment_thread") as run_deployment,
            patch.object(job_worker.threading, "Thread") as thread_class,
        ):
            job_worker._start_pending_app_deployment(label="")
            job_worker._start_pending_app_deployment(label="")
            job_worker._start_pending_app_deployment(label="")

            self.assertEqual(claim.call_count, 2)
            self.assertEqual(thread_class.call_count, 2)

            self._run_started_thread(thread_class=thread_class, call_index=0)
            job_worker._start_pending_app_deployment(label="")

        self.assertEqual(claim.call_count, 3)
        self.assertEqual(thread_class.call_count, 3)
        run_deployment.assert_called_once_with(app_id=str(jobs[0].id))

    def test_empty_app_queue_returns_slot(self) -> None:
        job = self._job()
        job_worker._cdk_job_slots = threading.BoundedSemaphore(value=1)

        with (
            patch.object(job_worker, "_claim_pending_app_deployment", side_effect=[None, job]) as claim,
            patch.object(job_worker.threading, "Thread") as thread_class,
        ):
            job_worker._start_pending_app_deployment(label="")
            job_worker._start_pending_app_deployment(label="")

        self.assertEqual(claim.call_count, 2)
        thread_class.assert_called_once()

    def test_failed_app_deployment_returns_slot(self) -> None:
        jobs = [self._job(), self._job()]
        job_worker._cdk_job_slots = threading.BoundedSemaphore(value=1)

        with (
            patch.object(job_worker, "_claim_pending_app_deployment", side_effect=jobs) as claim,
            patch.object(job_worker, "_run_app_deployment_thread", side_effect=RuntimeError("failed")),
            patch.object(job_worker.threading, "Thread") as thread_class,
        ):
            job_worker._start_pending_app_deployment(label="")
            with self.assertRaisesRegex(RuntimeError, "failed"):
                self._run_started_thread(thread_class=thread_class, call_index=0)
            job_worker._start_pending_app_deployment(label="")

        self.assertEqual(claim.call_count, 2)
        self.assertEqual(thread_class.call_count, 2)

    def test_environment_provisioning_shares_app_deployment_slots(self) -> None:
        deployment = self._job()
        environment = self._job()
        job_worker._cdk_job_slots = threading.BoundedSemaphore(value=1)

        with (
            patch.object(job_worker, "_claim_pending_app_deployment", return_value=deployment),
            patch.object(job_worker, "_claim_pending_environment_provisioning", return_value=environment) as claim_environment,
            patch.object(job_worker, "_run_app_deployment_thread"),
            patch.object(job_worker.threading, "Thread") as thread_class,
        ):
            job_worker._start_pending_app_deployment(label="")
            job_worker._start_pending_environment_provisioning()
            claim_environment.assert_not_called()

            self._run_started_thread(thread_class=thread_class, call_index=0)
            job_worker._start_pending_environment_provisioning()

        claim_environment.assert_called_once_with()
        self.assertEqual(thread_class.call_count, 2)
