"""Tests for environment setup draft, provisioning, and SSE behavior."""

from asgiref.sync import async_to_sync
from django.test import TestCase

import devopshero_app.models as models
import devopshero_app.services.agent.agent_build_prompt as agent_build_prompt
import devopshero_app.services.agent.agent_service as agent_service
import devopshero_app.services.agent.tools as agent_tools
import devopshero_app.views.chat as chat_views


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
        self.assertEqual(environment.vpc_stack_name, "devopshero-default-vpc")
        self.assertEqual(environment.cluster_stack_name, "devopshero-default-cluster")

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

    def test_save_environment_tool_result_emits_editor_refresh_notifications(self) -> None:
        event = agent_service.AgentStreamEvent(
            type="tool_result",
            data={
                "name": "mcp__devopshero__save_environment",
                "result": {
                    "id": "env-123",
                    "created": True,
                },
            },
        )

        sse_payload = chat_views._format_sse_event(event=event, show_costs=False)

        self.assertIn('"event": "environment-created"', sse_payload)
        self.assertIn('"environment_id": "env-123"', sse_payload)
        self.assertIn('"event": "environment-changed-env-123"', sse_payload)
