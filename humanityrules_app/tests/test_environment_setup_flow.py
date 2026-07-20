"""Tests for the surviving environment-setup logic: exclusive zone ownership and the provisioning gate.

The user-facing form path is covered by test_environment_setup_form; this pins the model/service
logic that path relies on.
"""

from asgiref.sync import async_to_sync
from django.test import TestCase

import humanityrules_app.models as models
from humanityrules_app.services.jobs import environment_operation_gate


class TestEnvironmentZoneOwnership(TestCase):
    """An environment owns its hosted zone exclusively within an AWS account."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Environment Org", slug="environment-org")
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Environment AWS",
            aws_account_id="123456789012",
            status=models.AWSAccount.Status.CONNECTED,
        )

    def test_zone_claimants_reports_the_owning_environment(self) -> None:
        claimer = models.Environment.objects.create(
            aws_account=self.aws_account, name="Claimer", slug="claimer",
            aws_region="us-east-1", status=models.Environment.Status.READY,
            shared_alb_hosted_zone="example.com",
        )

        claimants = models.Environment.zone_claimants(aws_account=self.aws_account).filter(
            shared_alb_hosted_zone="example.com",
        )

        self.assertEqual(list(claimants), [claimer])

    def test_zone_claimants_excludes_discarded_and_zoneless_environments(self) -> None:
        models.Environment.objects.create(
            aws_account=self.aws_account, name="Discarded", slug="discarded",
            aws_region="us-east-1", status=models.Environment.Status.DISCARDED,
            shared_alb_hosted_zone="example.com",
        )
        models.Environment.objects.create(
            aws_account=self.aws_account, name="Zoneless", slug="zoneless",
            aws_region="us-east-1", status=models.Environment.Status.READY,
            shared_alb_hosted_zone="",
        )

        self.assertFalse(models.Environment.zone_claimants(aws_account=self.aws_account).exists())


class TestEnvironmentProvisioningGate(TestCase):
    """A draft environment transitions to PENDING for the job worker via the operation gate."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Gate Org", slug="gate-org")
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Gate AWS",
            aws_account_id="123456789012",
            status=models.AWSAccount.Status.CONNECTED,
        )

    def test_draft_transitions_to_pending(self) -> None:
        environment = models.Environment.objects.create(
            aws_account=self.aws_account, name="Default", slug="default",
            aws_region="us-east-1", status=models.Environment.Status.DRAFT,
        )

        transitioned = async_to_sync(environment_operation_gate.atransition_environment_status)(
            environment_id=environment.id,
            expected_statuses=(models.Environment.Status.DRAFT,),
            new_status=models.Environment.Status.PENDING,
            status_message="Queued for provisioning",
        )

        environment.refresh_from_db()
        self.assertTrue(transitioned)
        self.assertEqual(environment.status, models.Environment.Status.PENDING)
        self.assertEqual(environment.status_message, "Queued for provisioning")

    def test_transition_is_a_noop_when_source_status_does_not_match(self) -> None:
        environment = models.Environment.objects.create(
            aws_account=self.aws_account, name="Ready", slug="ready",
            aws_region="us-east-1", status=models.Environment.Status.READY,
        )

        transitioned = async_to_sync(environment_operation_gate.atransition_environment_status)(
            environment_id=environment.id,
            expected_statuses=(models.Environment.Status.DRAFT,),
            new_status=models.Environment.Status.PENDING,
            status_message="Queued for provisioning",
        )

        environment.refresh_from_db()
        self.assertFalse(transitioned)
        self.assertEqual(environment.status, models.Environment.Status.READY)
