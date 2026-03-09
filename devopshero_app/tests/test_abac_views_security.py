"""ABAC view tests: Security settings and permissions editor endpoints.

Security settings (people, groups, policies) require org admin.
Permissions editor (approve) requires environment:approve.
"""

import json

from django.test import TestCase

from devopshero_app.models import (
    AWSAccount,
    App,
    AppPermissionRequest,
    Environment,
    Group,
    GroupAttribute,
    GroupMembership,
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    Repository,
    ResourceTag,
    User,
    Workspace,
)
from devopshero_app.services import abac

HTMX = {"HTTP_HX_REQUEST": "true"}


# ---------------------------------------------------------------------------
# Security Settings Endpoints (all require org admin)
# ---------------------------------------------------------------------------


class TestSecuritySettingsEndpoints(TestCase):
    """Every security settings endpoint requires org admin. Non-admins get 403."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Sec Test Org", slug="sec-test-org")

        self.admin_user = User.objects.create_user(username="sec_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin_user, role=OrganizationMembership.Role.ADMIN)
        abac.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

        self.regular_user = User.objects.create_user(username="sec_regular", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.regular_user, role=OrganizationMembership.Role.MEMBER)

        self.group = Group.objects.create(organization=self.org, name="Test Group")
        self.group_attr = GroupAttribute.objects.create(group=self.group, key="team", value="test")
        self.group_membership = GroupMembership.objects.create(group=self.group, user=self.regular_user)

        self.identity_attr = IdentityAttribute.objects.create(
            organization=self.org, user=self.regular_user, key="test-attr", value="test-val",
        )

        self.policy = Policy.objects.create(
            organization=self.org, name="Test Policy", resource_type="workspace",
            identity_conditions=[{"key": "test", "value": "test"}],
            resource_conditions=[{"key": "test", "value": "test"}],
            actions=["workspace:view"],
        )

    # --- People List & Detail ---

    def test_admin_can_access_people_list(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/security/people/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_people_list(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get("/security/people/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_people_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"/security/people/{self.regular_user.id}/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_people_detail(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get(f"/security/people/{self.regular_user.id}/", **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- People Attribute Mutations ---

    def test_admin_can_add_attribute(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/attributes/add/",
            {"key": "new", "value": "val"},
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_add_attribute(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/attributes/add/",
            {"key": "new", "value": "val"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_remove_attribute(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/attributes/{self.identity_attr.id}/remove/",
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_remove_attribute(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/attributes/{self.identity_attr.id}/remove/",
        )
        self.assertEqual(response.status_code, 403)

    # --- People Group Membership Mutations ---

    def test_admin_can_add_person_to_group(self) -> None:
        new_group = Group.objects.create(organization=self.org, name="New Group")
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/groups/add/",
            {"group_id": str(new_group.id)},
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_add_person_to_group(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/groups/add/",
            {"group_id": str(self.group.id)},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_remove_person_from_group(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/groups/{self.group_membership.id}/remove/",
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_remove_person_from_group(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/groups/{self.group_membership.id}/remove/",
        )
        self.assertEqual(response.status_code, 403)

    # --- Groups List, Create, Detail, Delete ---

    def test_admin_can_access_groups_list(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/security/groups/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_groups_list(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get("/security/groups/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_group(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post("/security/groups/create/", {"name": "New Group 2"})
        self.assertEqual(response.status_code, 302)

    def test_non_admin_gets_403_on_create_group(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post("/security/groups/create/", {"name": "New Group 2"})
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_group_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"/security/groups/{self.group.id}/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_group_detail(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get(f"/security/groups/{self.group.id}/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_delete_group(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(f"/security/groups/{self.group.id}/delete/")
        self.assertEqual(response.status_code, 302)

    def test_non_admin_gets_403_on_delete_group(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(f"/security/groups/{self.group.id}/delete/")
        self.assertEqual(response.status_code, 403)

    # --- Group Attribute Mutations ---

    def test_admin_can_add_group_attribute(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/attributes/add/",
            {"key": "new-key", "value": "new-val"},
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_add_group_attribute(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/attributes/add/",
            {"key": "new-key", "value": "new-val"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_remove_group_attribute(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/attributes/{self.group_attr.id}/remove/",
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_remove_group_attribute(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/attributes/{self.group_attr.id}/remove/",
        )
        self.assertEqual(response.status_code, 403)

    # --- Group Member Mutations ---

    def test_admin_can_add_group_member(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/members/add/",
            {"user_id": str(self.admin_user.id)},
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_add_group_member(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/members/add/",
            {"user_id": str(self.admin_user.id)},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_remove_group_member(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/members/{self.group_membership.id}/remove/",
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_remove_group_member(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/members/{self.group_membership.id}/remove/",
        )
        self.assertEqual(response.status_code, 403)

    # --- Policies List, Create, Detail, Delete ---

    def test_admin_can_access_policies_list(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/security/policies/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_policies_list(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get("/security/policies/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_policy(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post("/security/policies/create/", {
            "name": "New Policy",
            "resource_type": "workspace",
            "identity_conditions": json.dumps([{"key": "role", "value": "dev"}]),
            "resource_conditions": json.dumps([{"key": "domain", "value": "eng"}]),
            "actions": json.dumps(["workspace:view"]),
        })
        self.assertEqual(response.status_code, 204)

    def test_non_admin_gets_403_on_create_policy(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post("/security/policies/create/", {
            "name": "New Policy",
            "resource_type": "workspace",
            "identity_conditions": json.dumps([{"key": "role", "value": "dev"}]),
            "resource_conditions": json.dumps([{"key": "domain", "value": "eng"}]),
            "actions": json.dumps(["workspace:view"]),
        })
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_policy_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"/security/policies/{self.policy.id}/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_policy_detail(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get(f"/security/policies/{self.policy.id}/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_delete_policy(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(f"/security/policies/{self.policy.id}/delete/")
        self.assertEqual(response.status_code, 302)

    def test_non_admin_gets_403_on_delete_policy(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(f"/security/policies/{self.policy.id}/delete/")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Permissions Editor (environment:approve on target environment)
# ---------------------------------------------------------------------------


class TestPermissionsEditorEndpoints(TestCase):
    """Approving an AppPermissionRequest requires environment:approve."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="PE Test Org", slug="pe-test-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")
        self.repo = Repository.objects.create(
            organization=self.org, provider="github", name="repo",
            full_name="org/repo", clone_url="https://github.com/org/repo.git",
        )

        self.workspace = Workspace.objects.create(organization=self.org, name="WS", slug="ws")
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, repository=self.repo,
            name="PEApp", slug="peapp", app_type="web", build_strategy="dockerfile",
            branch="main", container_port=8000, health_check_path="/health",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Production", slug="production", aws_region="us-east-1",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=self.env,
            key="tier", value="production",
        )

        self.apr = AppPermissionRequest.objects.create(
            app=self.app, environment=self.env,
            statements=[{"effect": "Allow", "action": ["s3:GetObject"], "resource": ["*"]}],
            status=AppPermissionRequest.Status.DRAFT,
        )

        self.approver_user = User.objects.create_user(username="pe_approver", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.approver_user, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.approver_user, key="role", value="approver")
        Policy.objects.create(
            organization=self.org, name="Prod approvers", resource_type="environment",
            identity_conditions=[{"key": "role", "value": "approver"}],
            resource_conditions=[{"key": "tier", "value": "production"}],
            actions=["environment:approve"],
        )

        self.non_approver_user = User.objects.create_user(username="pe_nonapprover", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.non_approver_user, role=OrganizationMembership.Role.MEMBER)

    def test_approver_can_approve(self) -> None:
        self.client.force_login(self.approver_user)
        response = self.client.post(f"/security/permissions/{self.apr.id}/apply/")
        self.assertEqual(response.status_code, 200)

    def test_non_approver_gets_403_on_approve(self) -> None:
        self.client.force_login(self.non_approver_user)
        response = self.client.post(f"/security/permissions/{self.apr.id}/apply/")
        self.assertEqual(response.status_code, 403)
