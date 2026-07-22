"""ABAC view tests: Environment endpoints.

Environment list, detail, and tag management access control.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from humanityrules_app.models import (
    AWSAccount,
    Environment,
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    ResourceTag,
    User,
)
from humanityrules_app.services import abac_service
from humanityrules_app.services.sandbox_service import SANDBOX_ACCOUNT_NAME

HTMX = {"HTTP_HX_REQUEST": "true"}


class TestEnvironmentEndpoints(TestCase):
    """Environment list, detail, and tag management access control."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Env Test Org", slug="env-test-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")

        self.env_staging = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging", aws_region="us-east-1",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=self.env_staging,
            key="tier", value="staging",
        )
        self.env_production = Environment.objects.create(
            aws_account=self.aws_account, name="Production", slug="production", aws_region="us-east-1",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=self.env_production,
            key="tier", value="production",
        )

        self.admin_user = User.objects.create_user(username="env_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin_user, role=OrganizationMembership.Role.ADMIN)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

        self.viewer_user = User.objects.create_user(username="env_viewer", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.viewer_user, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.viewer_user, key="role", value="env-viewer")
        Policy.objects.create(
            organization=self.org, name="Staging viewers", resource_type="environment",
            identity_conditions=[{"key": "role", "value": "env-viewer"}],
            resource_conditions=[{"key": "tier", "value": "staging"}],
            actions=["environment:view"],
        )

        self.env_admin_user = User.objects.create_user(username="env_envadmin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.env_admin_user, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.env_admin_user, key="role", value="env-admin")
        Policy.objects.create(
            organization=self.org, name="Staging admins", resource_type="environment",
            identity_conditions=[{"key": "role", "value": "env-admin"}],
            resource_conditions=[{"key": "tier", "value": "staging"}],
            actions=["environment:admin"],
        )

        self.no_access_user = User.objects.create_user(username="env_noaccess", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.no_access_user, role=OrganizationMembership.Role.MEMBER)

    # --- Environment List (filter_permitted_resources) ---

    def _detail_url(self, environment: Environment) -> str:
        """Build the environment detail URL."""
        return reverse("environment_detail", kwargs={"environment_id": environment.id})

    def _tag_save_url(self, environment: Environment) -> str:
        """Build the environment tag-save URL."""
        return reverse("environment_tags_save", kwargs={"environment_id": environment.id})

    def _status_url(self, environment: Environment) -> str:
        """Build the environment status-fragment URL."""
        return reverse("environment_status", kwargs={"environment_id": environment.id})

    def test_admin_environment_list_shows_all(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/environments/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = {environment.pk for environment in response.context["environments"]}
        self.assertIn(self.env_staging.pk, pks)
        self.assertIn(self.env_production.pk, pks)

    def test_viewer_environment_list_only_shows_permitted(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/environments/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = {environment.pk for environment in response.context["environments"]}
        self.assertIn(self.env_staging.pk, pks)
        self.assertNotIn(self.env_production.pk, pks)

    # --- Environment Detail ---

    def test_admin_can_view_environment_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(self._detail_url(environment=self.env_staging), **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_viewer_can_view_environment_detail(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get(self._detail_url(environment=self.env_staging), **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_view_unmatched_environment(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get(self._detail_url(environment=self.env_production), **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_no_access_gets_403_on_environment_detail(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get(self._detail_url(environment=self.env_staging), **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- Environment Status fragment (self-terminating poll + OOB header actions) ---

    def test_status_fragment_polls_while_transient(self) -> None:
        self.env_staging.status = Environment.Status.PROVISIONING
        self.env_staging.save(update_fields=["status"])
        self.client.force_login(self.admin_user)
        response = self.client.get(self._status_url(environment=self.env_staging), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hx-trigger="load delay:5s"')

    def test_status_fragment_stops_polling_when_settled(self) -> None:
        self.env_staging.status = Environment.Status.READY
        self.env_staging.save(update_fields=["status"])
        self.client.force_login(self.admin_user)
        response = self.client.get(self._status_url(environment=self.env_staging), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "hx-trigger")

    def test_status_fragment_oob_swaps_header_actions_for_admin(self) -> None:
        self.env_staging.status = Environment.Status.READY
        self.env_staging.save(update_fields=["status"])
        self.client.force_login(self.admin_user)
        response = self.client.get(self._status_url(environment=self.env_staging), **HTMX)
        self.assertContains(response, 'id="environment-actions"')
        self.assertContains(response, 'hx-swap-oob="true"')
        self.assertContains(response, "Tear Down")

    def test_status_fragment_omits_header_actions_for_non_admin(self) -> None:
        self.env_staging.status = Environment.Status.READY
        self.env_staging.save(update_fields=["status"])
        self.client.force_login(self.viewer_user)
        response = self.client.get(self._status_url(environment=self.env_staging), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "hx-swap-oob")

    def test_viewer_gets_403_on_unmatched_environment_status(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get(self._status_url(environment=self.env_production), **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_status_poll_redirects_to_list_when_environment_gone(self) -> None:
        status_url = self._status_url(environment=self.env_staging)
        self.client.force_login(self.admin_user)
        self.env_staging.delete()
        response = self.client.get(status_url, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], reverse("environments"))

    def test_provisioning_log_poll_redirects_to_list_when_environment_gone(self) -> None:
        log_url = reverse("environment_provisioning_log", kwargs={"environment_id": self.env_staging.id})
        self.client.force_login(self.admin_user)
        self.env_staging.delete()
        response = self.client.get(log_url, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], reverse("environments"))

    # --- Sandbox account excluded from environment creation ---

    def _make_sandbox_account(self) -> AWSAccount:
        """The org's connected HUMR sandbox account (the Organization post_save signal may already have made it)."""
        account, _ = AWSAccount.objects.get_or_create(
            organization=self.org, name=SANDBOX_ACCOUNT_NAME,
            defaults={
                "aws_account_id": "999988887777",
                "status": AWSAccount.Status.CONNECTED,
                "is_humr_sandbox": True,
            },
        )
        return account

    def _make_connected_account(self) -> AWSAccount:
        """A connected, non-sandbox AWS account eligible for environment creation."""
        return AWSAccount.objects.create(
            organization=self.org, name="Connected AWS",
            aws_account_id="111122223333", status=AWSAccount.Status.CONNECTED,
        )

    def test_new_environment_card_excludes_sandbox_account(self) -> None:
        regular = self._make_connected_account()
        sandbox = self._make_sandbox_account()
        self.client.force_login(self.admin_user)
        response = self.client.get("/environments/", **HTMX)
        self.assertEqual(response.status_code, 200)
        account_ids = {account.id for account in response.context["aws_accounts"]}
        self.assertIn(regular.id, account_ids)
        self.assertNotIn(sandbox.id, account_ids)

    @patch("humanityrules_app.views.environments._list_hosted_zone_options", return_value=([{"id": "", "name": "None (HTTP only)"}], False))
    def test_setup_form_dropdown_excludes_sandbox_account(self, _mock_zones) -> None:
        regular = self._make_connected_account()
        sandbox = self._make_sandbox_account()
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environment_setup_form"), **HTMX)
        self.assertEqual(response.status_code, 200)
        option_ids = {option["id"] for option in response.context["account_options"]}
        self.assertIn(str(regular.id), option_ids)
        self.assertNotIn(str(sandbox.id), option_ids)

    def test_setup_form_post_rejects_sandbox_account(self) -> None:
        sandbox = self._make_sandbox_account()
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("environment_setup_form"),
            {"aws_account": str(sandbox.id), "name": "Blah", "aws_region": "us-east-1"},
            **HTMX,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Environment.objects.filter(aws_account=sandbox, slug="blah").exists())

    # --- Environment Tag Management (requires environment:admin) ---

    def test_admin_can_bulk_save_environment_tags(self) -> None:
        import json
        self.client.force_login(self.admin_user)
        response = self.client.post(
            self._tag_save_url(environment=self.env_staging),
            {"tags": json.dumps([{"key": "tier", "value": "staging"}, {"key": "region", "value": "us-east-1"}])},
        )
        self.assertEqual(response.status_code, 200)
        tags = ResourceTag.objects.filter(environment=self.env_staging).order_by("key")
        self.assertEqual(tags.count(), 2)
        self.assertEqual(tags[0].key, "region")

    def test_viewer_gets_403_on_environment_tags_save(self) -> None:
        import json
        self.client.force_login(self.viewer_user)
        response = self.client.post(
            self._tag_save_url(environment=self.env_staging),
            {"tags": json.dumps([{"key": "tier", "value": "staging"}])},
        )
        self.assertEqual(response.status_code, 403)
