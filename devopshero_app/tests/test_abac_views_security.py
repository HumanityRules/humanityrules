"""ABAC view tests: Security settings and permissions editor endpoints.

Security settings (people, groups, policies) require org admin.
Permissions editor (approve) requires environment:approve.
"""

import json
from unittest.mock import patch

from django.test import TestCase

from devopshero_app.models import (
    AWSAccount,
    App,
    AppPermissionRequest,
    Conversation,
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

    # --- Cross-org user_id regression (must not leak across tenants) ---

    def test_people_detail_404_for_stranger_user_id(self) -> None:
        # A user from another org is not visible — even to an admin of *this* org.
        other_org = Organization.objects.create(name="Other Org", slug="other-org-people")
        stranger = User.objects.create_user(
            username="stranger", password="x", current_organization=other_org,
        )
        OrganizationMembership.objects.create(
            organization=other_org, user=stranger, role=OrganizationMembership.Role.MEMBER,
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(f"/security/people/{stranger.id}/", **HTMX)
        self.assertEqual(response.status_code, 404)

    def test_people_attribute_add_404_for_stranger_user_id(self) -> None:
        other_org = Organization.objects.create(name="Other Org", slug="other-org-attr")
        stranger = User.objects.create_user(
            username="stranger-attr", password="x", current_organization=other_org,
        )
        OrganizationMembership.objects.create(
            organization=other_org, user=stranger, role=OrganizationMembership.Role.MEMBER,
        )

        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/people/{stranger.id}/attributes/add/",
            {"key": "k", "value": "v"},
        )
        self.assertEqual(response.status_code, 404)
        # And no attribute leaked into our org against the stranger.
        self.assertFalse(
            IdentityAttribute.objects.filter(user=stranger).exists()
        )

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

    def test_group_member_add_404_for_stranger_user_id(self) -> None:
        # Admin tries to add a stranger (different org's user) to a group in this org.
        other_org = Organization.objects.create(name="Other Org", slug="other-org-gm")
        stranger = User.objects.create_user(
            username="stranger-gm", password="x", current_organization=other_org,
        )
        OrganizationMembership.objects.create(
            organization=other_org, user=stranger, role=OrganizationMembership.Role.MEMBER,
        )

        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/members/add/",
            {"user_id": str(stranger.id)},
        )
        self.assertEqual(response.status_code, 404)
        # And no membership row leaked.
        self.assertFalse(
            GroupMembership.objects.filter(group=self.group, user=stranger).exists()
        )

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

    def test_permissions_editor_uses_per_user_conversations(self) -> None:
        # Two users from the same org open the editor for the same app+env.
        # Each must get their own Conversation — the second visitor must NOT
        # inherit the first visitor's LLM chat history.
        editor_url = (
            f"/security/permissions/editor/?context_app={self.app.slug}"
            f"&context_environment={self.env.slug}"
        )

        # Mock AWS calls so the view doesn't try to assume roles during tests.
        with patch(
            "devopshero_app.services.permissions_service.iam_utils.read_app_permissions_policy",
            return_value=[],
        ), patch(
            "devopshero_app.services.permissions_service.iam_utils.list_resources_for_services",
            return_value={},
        ):
            self.client.force_login(self.approver_user)
            response_a = self.client.get(editor_url, **HTMX)
            self.assertEqual(response_a.status_code, 200)

            self.client.force_login(self.non_approver_user)
            response_b = self.client.get(editor_url, **HTMX)
            self.assertEqual(response_b.status_code, 200)

        conversations = Conversation.objects.filter(
            context_app_permission_request__app=self.app,
            context_app_permission_request__environment=self.env,
        )
        self.assertEqual(conversations.count(), 2)
        user_ids = set(conversations.values_list("user_id", flat=True))
        self.assertEqual(user_ids, {self.approver_user.id, self.non_approver_user.id})
