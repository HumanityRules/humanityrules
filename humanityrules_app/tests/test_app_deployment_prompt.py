"""Tests for app deployment prompt guidance."""

from asgiref.sync import async_to_sync
from django.test import TestCase

import humanityrules_app.models as models
import humanityrules_app.services.agent.agent_build_prompt as agent_build_prompt


class TestAppDeploymentPrompt(TestCase):
    """Verify the app deployment prompt includes key guardrails."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Prompt Org", slug="prompt-org")
        self.user = models.User.objects.create_user(
            username="prompt-user",
            password="x",
            current_organization=self.organization,
        )
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Prompt AWS")
        self.repository = models.Repository.objects.create(
            organization=self.organization,
            provider="github",
            name="repo",
            full_name="org/repo",
            default_branch="main",
            clone_url="https://github.com/org/repo.git",
        )
        self.workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Engineering",
            slug="engineering",
        )
        models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Production",
            slug="production",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
            shared_alb_hosted_zone="example.com",
        )
        models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
            shared_alb_hosted_zone="example.com",
        )
        self.conversation = models.Conversation.objects.create(
            user=self.user,
            organization=self.organization,
            context_repository=self.repository,
            context_workspace=self.workspace,
            mode=models.Conversation.Mode.APP_DEPLOYMENT,
        )

    def test_prompt_forbids_combined_redeploy_options(self) -> None:
        prompt = async_to_sync(agent_build_prompt.build_system_prompt)(conversation=self.conversation)

        self.assertIn("Every option must target exactly one environment", prompt)
        self.assertIn("Re-deploy to both", prompt)
        self.assertIn("Never offer combined options like `Both`, `All environments`", prompt)
        self.assertIn("handle them as separate deployments with one environment choice at a time", prompt)
