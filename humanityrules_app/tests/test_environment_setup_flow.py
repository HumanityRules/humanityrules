"""Tests for environment setup draft, provisioning, and SSE behavior."""

from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase

import humanityrules_app.models as models
import humanityrules_app.services.agent.agent_build_prompt as agent_build_prompt
import humanityrules_app.services.agent.tools as agent_tools


class TestEnvironmentSetupFlow(TestCase):
    """Verify the draft-first environment setup flow."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Environment Org", slug="environment-org")
        self.user = models.User.objects.create_user(
            username="environment-user",
            password="x",
            current_organization=self.organization,
        )
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Environment AWS",
            aws_account_id="123456789012",
            status=models.AWSAccount.Status.CONNECTED,
        )
        self.conversation = models.Conversation.objects.create(
            user=self.user,
            organization=self.organization,
            context_aws_account=self.aws_account,
            mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
        )

    def test_save_environment_creates_draft_and_pins_conversation(self) -> None:
        result = async_to_sync(agent_tools.save_environment)(
            conversation=self.conversation,
            aws_account=self.aws_account,
            environment_name="Default",
            aws_region="us-east-1",
            hosted_zone_name="example.com",
        )

        self.conversation.refresh_from_db()
        environment = models.Environment.objects.get(id=self.conversation.context_environment_id)

        self.assertTrue(result.created)
        self.assertEqual(environment.status, models.Environment.Status.DRAFT)
        self.assertEqual(environment.shared_alb_hosted_zone, "example.com")
        self.assertEqual(environment.vpc_stack_name, "humr-default-vpc")
        self.assertEqual(environment.cluster_stack_name, "humr-default-cluster")

    def test_provision_environment_moves_saved_draft_to_pending(self) -> None:
        async_to_sync(agent_tools.save_environment)(
            conversation=self.conversation,
            aws_account=self.aws_account,
            environment_name="Default",
            aws_region="us-east-1",
            hosted_zone_name="",
        )

        result = async_to_sync(agent_tools.provision_environment)(
            conversation=self.conversation,
            organization=self.organization,
        )

        self.conversation.refresh_from_db()
        environment = models.Environment.objects.get(id=self.conversation.context_environment_id)

        self.assertEqual(result.status, models.Environment.Status.PENDING)
        self.assertEqual(environment.status, models.Environment.Status.PENDING)
        self.assertEqual(environment.status_message, "Queued for provisioning")

    def test_save_environment_rejects_zone_claimed_by_another_environment(self) -> None:
        models.Environment.objects.create(
            aws_account=self.aws_account, name="Claimer", slug="claimer",
            aws_region="us-east-1", status=models.Environment.Status.READY,
            shared_alb_hosted_zone="example.com",
        )
        with self.assertRaisesMessage(ValueError, "already belongs to environment 'Claimer'"):
            async_to_sync(agent_tools.save_environment)(
                conversation=self.conversation,
                aws_account=self.aws_account,
                environment_name="Default",
                aws_region="us-east-1",
                hosted_zone_name="example.com",
            )
        self.assertFalse(models.Environment.objects.filter(aws_account=self.aws_account, slug="default").exists())

    def test_save_environment_update_keeps_its_own_zone(self) -> None:
        async_to_sync(agent_tools.save_environment)(
            conversation=self.conversation,
            aws_account=self.aws_account,
            environment_name="Default",
            aws_region="us-east-1",
            hosted_zone_name="example.com",
        )
        result = async_to_sync(agent_tools.save_environment)(
            conversation=self.conversation,
            aws_account=self.aws_account,
            environment_name="Default",
            aws_region="eu-west-1",
            hosted_zone_name="example.com",
        )
        self.assertFalse(result.created)
        self.assertEqual(result.shared_alb_hosted_zone, "example.com")
        self.assertEqual(result.aws_region, "eu-west-1")

    def test_list_hosted_zones_annotates_claimed_zones(self) -> None:
        models.Environment.objects.create(
            aws_account=self.aws_account, name="Claimer", slug="claimer",
            aws_region="us-east-1", status=models.Environment.Status.READY,
            shared_alb_hosted_zone="example.com",
        )
        zones_stub = [
            {"id": "Z1", "name": "example.com.", "record_count": 4},
            {"id": "Z2", "name": "free.example.com.", "record_count": 2},
        ]
        route53_zones_path = "humanityrules_app.services.infra_customer.route53_utils.list_hosted_zones"
        assume_role_path = "humanityrules_app.services.infra_customer.iam_utils.get_assumed_role_session"
        with patch(route53_zones_path, return_value=zones_stub), patch(assume_role_path):
            summaries = async_to_sync(agent_tools.list_hosted_zones)(
                aws_account_uuid=str(self.aws_account.id),
                organization=self.organization,
            )
        by_name = {summary.name: summary for summary in summaries}
        self.assertEqual(by_name["example.com."].in_use_by_environment, "Claimer")
        self.assertIsNone(by_name["free.example.com."].in_use_by_environment)

    def test_provision_environment_requires_saved_draft(self) -> None:
        with self.assertRaisesMessage(ValueError, "Use save_environment first"):
            async_to_sync(agent_tools.provision_environment)(
                conversation=self.conversation,
                organization=self.organization,
            )

    def test_environment_prompt_requires_saved_draft_review_before_provision(self) -> None:
        prompt = async_to_sync(agent_build_prompt.build_system_prompt)(conversation=self.conversation)

        self.assertIn("call `save_environment`", prompt)
        self.assertIn("without asking for a pre-save confirmation turn", prompt)
        self.assertIn("Do NOT ask the user to approve or confirm the setup before calling `save_environment`", prompt)
        self.assertIn("Save first, then let the user revise the saved draft if needed", prompt)
        self.assertIn("Everything looks good. Provision this environment now?", prompt)
        self.assertIn("Keep editing", prompt)
        self.assertIn("This `AskUserQuestion` step is mandatory, not optional", prompt)
        self.assertIn("MUST use `AskUserQuestion` for the domain choice once the list is known", prompt)
        self.assertIn("Do NOT ask the user to type the domain choice when you already know the available options", prompt)
        self.assertIn("Do NOT call `provision_environment` in the same turn as `save_environment`", prompt)
        self.assertNotIn("Does this look good? Let me know if you'd like different settings.", prompt)
