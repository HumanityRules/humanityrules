"""ABAC view tests: Environment endpoints.

Environment list, detail, and tag management access control.
"""

from django.test import TestCase

from devopshero_app.models import (
    AWSAccount,
    Environment,
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    ResourceTag,
    User,
)
from devopshero_app.services import abac

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
        abac.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

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

        self.removable_tag = ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=self.env_staging,
            key="removable", value="yes",
        )

    # --- Environment List (filter_permitted_resources) ---

    def test_admin_environment_list_shows_all(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/environments/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = set(response.context["environments"].values_list("pk", flat=True))
        self.assertIn(self.env_staging.pk, pks)
        self.assertIn(self.env_production.pk, pks)

    def test_viewer_environment_list_only_shows_permitted(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/environments/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = set(response.context["environments"].values_list("pk", flat=True))
        self.assertIn(self.env_staging.pk, pks)
        self.assertNotIn(self.env_production.pk, pks)

    # --- Environment Detail ---

    def test_admin_can_view_environment_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/environments/staging/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_viewer_can_view_environment_detail(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/environments/staging/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_view_unmatched_environment(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/environments/production/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_no_access_gets_403_on_environment_detail(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/environments/staging/", **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- Environment Tag Management (requires environment:admin) ---

    def test_admin_can_add_environment_tag(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post("/environments/staging/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 200)

    def test_viewer_gets_403_on_environment_tag_add(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.post("/environments/staging/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 403)

    def test_env_admin_can_add_environment_tag(self) -> None:
        self.client.force_login(self.env_admin_user)
        response = self.client.post("/environments/staging/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 200)

    def test_admin_can_remove_environment_tag(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(f"/environments/staging/tags/{self.removable_tag.id}/remove/")
        self.assertEqual(response.status_code, 200)

    def test_viewer_gets_403_on_environment_tag_remove(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.post(f"/environments/staging/tags/{self.removable_tag.id}/remove/")
        self.assertEqual(response.status_code, 403)

    def test_admin_can_bulk_save_environment_tags(self) -> None:
        import json
        self.client.force_login(self.admin_user)
        response = self.client.post(
            "/environments/staging/tags/save/",
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
            "/environments/staging/tags/save/",
            {"tags": json.dumps([{"key": "tier", "value": "staging"}])},
        )
        self.assertEqual(response.status_code, 403)
