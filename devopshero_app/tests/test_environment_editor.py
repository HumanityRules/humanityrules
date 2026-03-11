"""Tests for the environment setup editor views and entrypoints."""

from django.test import TestCase
from django.urls import reverse

import devopshero_app.models as models
from devopshero_app.services import abac

HTMX = {"HTTP_HX_REQUEST": "true"}


class TestEnvironmentEditor(TestCase):
    """Verify environment setup editor routing and lifecycle behavior."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Environment Editor Org", slug="environment-editor-org")
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Editor AWS",
            aws_account_id="123456789012",
            status=models.AWSAccount.Status.CONNECTED,
        )
        self.env_draft = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Draft Environment",
            slug="draft-environment",
            aws_region="us-east-1",
            status=models.Environment.Status.DRAFT,
        )
        self.env_ready = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Ready Environment",
            slug="ready-environment",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
        )
        models.ResourceTag.objects.create(
            organization=self.organization,
            resource_type="environment",
            environment=self.env_draft,
            key="visibility",
            value="allowed",
        )
        models.ResourceTag.objects.create(
            organization=self.organization,
            resource_type="environment",
            environment=self.env_ready,
            key="visibility",
            value="allowed",
        )

        self.admin_user = models.User.objects.create_user(
            username="environment-editor-admin",
            password="x",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.admin_user,
            role=models.OrganizationMembership.Role.ADMIN,
        )
        abac.bootstrap_organization(organization=self.organization, admin_user=self.admin_user)

        self.viewer_user = models.User.objects.create_user(
            username="environment-editor-viewer",
            password="x",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.viewer_user,
            role=models.OrganizationMembership.Role.MEMBER,
        )
        models.IdentityAttribute.objects.create(
            organization=self.organization,
            user=self.viewer_user,
            key="role",
            value="env-viewer",
        )
        models.Policy.objects.create(
            organization=self.organization,
            name="Environment viewers",
            resource_type="environment",
            identity_conditions=[{"key": "role", "value": "env-viewer"}],
            resource_conditions=[{"key": "visibility", "value": "allowed"}],
            actions=["environment:view"],
        )

    def test_environment_editor_new_creates_account_scoped_conversation(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"{reverse('environment_editor_new')}?aws_account={self.aws_account.id}", **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["environment"])
        self.assertEqual(response.context["conversation"].context_aws_account, self.aws_account)
        self.assertEqual(response.context["conversation"].mode, models.Conversation.Mode.ENVIRONMENT_SETUP)

    def test_environment_editor_resumes_latest_environment_conversation(self) -> None:
        conversation = models.Conversation.objects.create(
            user=self.admin_user,
            organization=self.organization,
            context_aws_account=self.aws_account,
            context_environment=self.env_draft,
            mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("environment_editor", kwargs={"environment_id": self.env_draft.id}),
            **HTMX,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["conversation"].id, conversation.id)

    def test_admin_environment_cards_route_incomplete_envs_to_setup_editor(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environments"), **HTMX)

        self.assertEqual(response.status_code, 200)
        environments = {environment.id: environment for environment in response.context["environments"]}
        self.assertEqual(
            environments[self.env_draft.id].primary_url,
            reverse("environment_editor", kwargs={"environment_id": self.env_draft.id}),
        )
        self.assertEqual(
            environments[self.env_ready.id].primary_url,
            reverse("environment_detail", kwargs={"environment_id": self.env_ready.id}),
        )

    def test_viewer_environment_cards_keep_detail_link_for_incomplete_envs(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get(reverse("environments"), **HTMX)

        self.assertEqual(response.status_code, 200)
        environments = {environment.id: environment for environment in response.context["environments"]}
        self.assertEqual(
            environments[self.env_draft.id].primary_url,
            reverse("environment_detail", kwargs={"environment_id": self.env_draft.id}),
        )

    def test_discard_draft_marks_environment_discarded_and_abandons_conversations(self) -> None:
        conversation = models.Conversation.objects.create(
            user=self.admin_user,
            organization=self.organization,
            context_aws_account=self.aws_account,
            context_environment=self.env_draft,
            mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
        )

        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("environment_editor_discard_draft", kwargs={"conversation_id": conversation.id}),
            **HTMX,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["HX-Push-Url"], reverse("environments"))
        self.env_draft.refresh_from_db()
        conversation.refresh_from_db()
        self.assertEqual(self.env_draft.status, models.Environment.Status.DISCARDED)
        self.assertEqual(conversation.status, models.Conversation.Status.ABANDONED)
