"""Tests for the ABAC policy evaluation engine (humanityrules_app/services/abac_service.py)."""

from django.db.models import QuerySet
from django.test import TestCase

from humanityrules_app.models import (
    AWSAccount,
    App,
    AppTemplate,
    Environment,
    Group,
    GroupAttribute,
    GroupMembership,
    IdentityAttribute,
    Organization,
    Policy,
    ResourceTag,
    User,
    Workspace,
)
from django.core.exceptions import ValidationError

from humanityrules_app.services import abac_service
from humanityrules_app.services.abac_service import _conditions_match as _raw_conditions_match
from humanityrules_app.tests import app_test_factories


def _conditions_match(conditions: list[dict], self_side: set[tuple[str, str]]) -> bool:
    """Test helper: the historical two-arg form, with references disallowed (no other side)."""
    return _raw_conditions_match(
        conditions=conditions,
        self_side=self_side,
        other_sides={},
    )


# ---------------------------------------------------------------------------
# 1.1 Effective Attributes
# ---------------------------------------------------------------------------


class TestGetEffectiveAttributes(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Test Org", slug="attr-org")
        self.user = User.objects.create_user(
            username="attruser", password="testpass", current_organization=self.org,
        )

    def test_no_attributes_returns_only_authenticated(self) -> None:
        attrs = abac_service.get_effective_attributes(organization=self.org, user=self.user)
        self.assertEqual(attrs, [("authenticated", "true", "system")])

    def test_direct_attributes_have_direct_source(self) -> None:
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="role", value="developer",
        )
        attrs = abac_service.get_effective_attributes(organization=self.org, user=self.user)
        self.assertIn(("authenticated", "true", "system"), attrs)
        self.assertIn(("role", "developer", "direct"), attrs)

    def test_group_attributes_have_group_source(self) -> None:
        group = Group.objects.create(organization=self.org, name="Engineers")
        GroupMembership.objects.create(group=group, user=self.user)
        GroupAttribute.objects.create(group=group, key="team", value="backend")

        attrs = abac_service.get_effective_attributes(organization=self.org, user=self.user)
        self.assertIn(("team", "backend", "group:Engineers"), attrs)

    def test_multiple_groups_returns_union(self) -> None:
        g1 = Group.objects.create(organization=self.org, name="Frontend")
        g2 = Group.objects.create(organization=self.org, name="Backend")
        GroupMembership.objects.create(group=g1, user=self.user)
        GroupMembership.objects.create(group=g2, user=self.user)
        GroupAttribute.objects.create(group=g1, key="team", value="frontend")
        GroupAttribute.objects.create(group=g2, key="team", value="backend")

        attrs = abac_service.get_effective_attributes(organization=self.org, user=self.user)
        self.assertIn(("team", "frontend", "group:Frontend"), attrs)
        self.assertIn(("team", "backend", "group:Backend"), attrs)

    def test_same_key_from_direct_and_group_not_deduplicated(self) -> None:
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="team", value="platform",
        )
        group = Group.objects.create(organization=self.org, name="DataTeam")
        GroupMembership.objects.create(group=group, user=self.user)
        GroupAttribute.objects.create(group=group, key="team", value="data")

        attrs = abac_service.get_effective_attributes(organization=self.org, user=self.user)
        self.assertIn(("team", "platform", "direct"), attrs)
        self.assertIn(("team", "data", "group:DataTeam"), attrs)


# ---------------------------------------------------------------------------
# 1.2 Effective Tags
# ---------------------------------------------------------------------------


class TestGetEffectiveTags(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Tag Org", slug="tag-org")
        self.user = User.objects.create_user(
            username="taguser", password="testpass", current_organization=self.org,
        )
        self.workspace = Workspace.objects.create(
            organization=self.org, name="TagWS", slug="tagws",
        )
        self.aws_account = AWSAccount.objects.create(
            organization=self.org, name="Test Account",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="TagEnv", slug="tagenv", aws_region="us-east-1",
        )

    def _make_app(self, name: str, slug: str) -> App:
        return App.objects.create(
            organization=self.org, workspace=self.workspace,
            source_template=app_test_factories.make_source_template(),
            environment=self.env, name=name, slug=slug,
            container_port=8000, health_check_path="/health",
            cpu=256, memory=512,
        )

    def test_app_direct_tags_source_is_direct(self) -> None:
        app = self._make_app(name="myapp", slug="myapp")
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=app,
            key="tier", value="production",
        )
        tags = abac_service.get_effective_tags(organization=self.org, resource=app, resource_type="app")
        direct_tags = [(k, v, s) for k, v, s in tags if s == "direct"]
        self.assertIn(("tier", "production", "direct"), direct_tags)

    def test_app_inherits_workspace_tags(self) -> None:
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="finance",
        )
        app = self._make_app(name="finapp", slug="finapp")

        tags = abac_service.get_effective_tags(organization=self.org, resource=app, resource_type="app")
        inherited = [(k, v, s) for k, v, s in tags if s.startswith("inherited:")]
        self.assertIn(("domain", "finance", f"inherited:{self.workspace.name}"), inherited)

    def test_app_returns_both_direct_and_inherited(self) -> None:
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="finance",
        )
        app = self._make_app(name="bothapp", slug="bothapp")
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=app,
            key="tier", value="production",
        )

        tags = abac_service.get_effective_tags(organization=self.org, resource=app, resource_type="app")
        self.assertIn(("tier", "production", "direct"), tags)
        self.assertIn(("domain", "finance", f"inherited:{self.workspace.name}"), tags)

    def test_workspace_tags_are_always_direct(self) -> None:
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="platform",
        )
        tags = abac_service.get_effective_tags(organization=self.org, resource=self.workspace, resource_type="workspace")
        self.assertTrue(all(source == "direct" for _, _, source in tags))
        self.assertIn(("domain", "platform", "direct"), tags)

    def test_environment_tags_are_always_direct(self) -> None:
        env = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging",
            aws_region="us-east-1",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=env,
            key="tier", value="staging",
        )

        tags = abac_service.get_effective_tags(organization=self.org, resource=env, resource_type="environment")
        self.assertTrue(all(source == "direct" for _, _, source in tags))
        self.assertIn(("tier", "staging", "direct"), tags)

    def test_foreign_org_tag_excluded_from_workspace(self) -> None:
        """A ResourceTag with mismatched organization is excluded by the org filter."""
        foreign_org = Organization.objects.create(name="Foreign Org", slug="foreign-org")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="engineering",
        )
        ResourceTag.objects.create(
            organization=foreign_org, resource_type="workspace", workspace=self.workspace,
            key="injected", value="malicious",
        )

        tags = abac_service.get_effective_tags(organization=self.org, resource=self.workspace, resource_type="workspace")
        keys = [k for k, _, _ in tags]
        self.assertIn("domain", keys)
        self.assertNotIn("injected", keys)

    def test_foreign_org_tag_excluded_from_app(self) -> None:
        """Foreign-org tags on an app and its workspace are both excluded."""
        foreign_org = Organization.objects.create(name="Foreign Org 2", slug="foreign-org-2")
        app = self._make_app(name="secapp", slug="secapp")
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=app,
            key="tier", value="production",
        )
        ResourceTag.objects.create(
            organization=foreign_org, resource_type="app", app=app,
            key="injected-app", value="bad",
        )
        ResourceTag.objects.create(
            organization=foreign_org, resource_type="workspace", workspace=self.workspace,
            key="injected-ws", value="bad",
        )

        tags = abac_service.get_effective_tags(organization=self.org, resource=app, resource_type="app")
        keys = [k for k, _, _ in tags]
        self.assertIn("tier", keys)
        self.assertNotIn("injected-app", keys)
        self.assertNotIn("injected-ws", keys)


# ---------------------------------------------------------------------------
# 1.3 Condition Matching
# ---------------------------------------------------------------------------


class TestConditionsMatch(TestCase):

    def test_wildcard_matches_empty_set(self) -> None:
        self.assertTrue(_conditions_match([{"key": "*", "value": "*"}], set()))

    def test_wildcard_matches_non_empty_set(self) -> None:
        self.assertTrue(_conditions_match(
            [{"key": "*", "value": "*"}], {("team", "backend")},
        ))

    def test_single_condition_present(self) -> None:
        self.assertTrue(_conditions_match(
            [{"key": "team", "value": "backend"}],
            {("team", "backend")},
        ))

    def test_single_condition_absent(self) -> None:
        self.assertFalse(_conditions_match(
            [{"key": "team", "value": "backend"}],
            {("team", "frontend")},
        ))

    def test_multiple_conditions_all_present(self) -> None:
        conditions = [
            {"key": "team", "value": "backend"},
            {"key": "role", "value": "developer"},
        ]
        attr_set = {("team", "backend"), ("role", "developer"), ("extra", "val")}
        self.assertTrue(_conditions_match(conditions, attr_set))

    def test_multiple_conditions_one_missing(self) -> None:
        conditions = [
            {"key": "team", "value": "backend"},
            {"key": "role", "value": "developer"},
        ]
        self.assertFalse(_conditions_match(conditions, {("team", "backend")}))

    def test_empty_conditions_vacuously_true(self) -> None:
        self.assertTrue(_conditions_match([], set()))
        self.assertTrue(_conditions_match([], {("a", "b")}))

    def test_mixed_wildcard_does_not_short_circuit(self) -> None:
        """Wildcard mixed with other conditions must NOT match as wildcard.

        The {"key":"*","value":"*"} entry becomes a literal condition requiring ("*","*")
        in the attribute set, which never exists. The policy is effectively dead — fail-closed.
        Validation prevents this combination from being created in the first place.
        """
        conditions = [{"key": "*", "value": "*"}, {"key": "team", "value": "finance"}]
        self.assertFalse(_conditions_match(conditions, set()))
        self.assertFalse(_conditions_match(conditions, {("role", "developer")}))
        self.assertFalse(_conditions_match(conditions, {("team", "finance")}))


# ---------------------------------------------------------------------------
# 1.3b Condition Validation
# ---------------------------------------------------------------------------


class TestValidatePolicyConditions(TestCase):

    def test_sole_wildcard_identity_is_valid(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "*", "value": "*"}],
            resource_conditions=[{"key": "domain", "value": "finance"}],
        )

    def test_sole_wildcard_resource_is_valid(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
        )

    def test_both_wildcard_is_valid(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "*", "value": "*"}],
            resource_conditions=[{"key": "*", "value": "*"}],
        )

    def test_empty_conditions_are_valid(self) -> None:
        abac_service.validate_policy_conditions(identity_conditions=[], resource_conditions=[])

    def test_mixed_wildcard_identity_raises(self) -> None:
        with self.assertRaises(ValidationError) as cm:
            abac_service.validate_policy_conditions(
                identity_conditions=[{"key": "*", "value": "*"}, {"key": "team", "value": "finance"}],
                resource_conditions=[{"key": "domain", "value": "finance"}],
            )
        self.assertIn("Identity", str(cm.exception))

    def test_mixed_wildcard_resource_raises(self) -> None:
        with self.assertRaises(ValidationError) as cm:
            abac_service.validate_policy_conditions(
                identity_conditions=[{"key": "role", "value": "developer"}],
                resource_conditions=[{"key": "*", "value": "*"}, {"key": "tier", "value": "production"}],
            )
        self.assertIn("Resource", str(cm.exception))

    def test_multiple_non_wildcard_conditions_valid(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "team", "value": "finance"}, {"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "finance"}, {"key": "tier", "value": "production"}],
        )


# ---------------------------------------------------------------------------
# 1.4 Policy Evaluation
# ---------------------------------------------------------------------------


class TestEvaluatePolicies(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Policy Org", slug="policy-org")
        self.user = User.objects.create_user(
            username="policyuser", password="testpass", current_organization=self.org,
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="role", value="developer",
        )
        self.workspace = Workspace.objects.create(
            organization=self.org, name="DevWS", slug="devws",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="engineering",
        )

    def test_matching_policy_grants_actions(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Dev workspace access",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertIn("workspace:view", result)

    def test_non_matching_identity_skipped(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Manager only",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "manager"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)

    def test_non_matching_resource_skipped(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Finance only",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "finance"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)

    def test_multiple_policies_union_of_grants(self) -> None:
        Policy.objects.create(
            organization=self.org, name="View access",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        Policy.objects.create(
            organization=self.org, name="Edit access",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:edit"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertIn("workspace:view", result)
        self.assertIn("workspace:edit", result)

    def test_hierarchy_workspace_admin_expands(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Admin access",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:admin"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertEqual(result, {"workspace:admin", "workspace:view", "workspace:edit"})

    def test_hierarchy_environment_admin_expands(self) -> None:
        aws_account = AWSAccount.objects.create(
            organization=self.org, name="Test Account",
        )
        env = Environment.objects.create(
            aws_account=aws_account, name="staging", slug="staging",
            aws_region="us-east-1",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=env,
            key="tier", value="staging",
        )
        Policy.objects.create(
            organization=self.org, name="Env admin",
            resource_type="environment",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "tier", "value": "staging"}],
            actions=["environment:admin"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=env, resource_type="environment",
        )
        self.assertEqual(
            result,
            {"environment:admin", "environment:view", "environment:deploy", "environment:approve"},
        )

    def test_deny_overrides_grant(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Grant view",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        Policy.objects.create(
            organization=self.org, name="Deny view",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["!workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)

    def test_deny_on_expanded_action(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Admin grant",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:admin"],
        )
        Policy.objects.create(
            organization=self.org, name="Deny view",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["!workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertIn("workspace:admin", result)
        self.assertIn("workspace:edit", result)
        self.assertNotIn("workspace:view", result)

    def test_no_policies_returns_empty(self) -> None:
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertEqual(result, set())

    def test_wildcard_identity_matches_any_user(self) -> None:
        bare_user = User.objects.create_user(
            username="bareuser", password="testpass", current_organization=self.org,
        )
        Policy.objects.create(
            organization=self.org, name="Anyone can view",
            resource_type="workspace",
            identity_conditions=[{"key": "*", "value": "*"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=bare_user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertIn("workspace:view", result)

    def test_wildcard_resource_matches_any_resource(self) -> None:
        bare_ws = Workspace.objects.create(
            organization=self.org, name="Empty WS", slug="emptyws",
        )
        Policy.objects.create(
            organization=self.org, name="All workspaces",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=bare_ws, resource_type="workspace",
        )
        self.assertIn("workspace:view", result)

    def test_empty_identity_conditions_matches_all_users(self) -> None:
        """Policy with identity_conditions=[] is vacuously true — matches every user."""
        bare_user = User.objects.create_user(
            username="emptyiduser", password="testpass", current_organization=self.org,
        )
        Policy.objects.create(
            organization=self.org, name="Empty identity policy",
            resource_type="workspace",
            identity_conditions=[],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=bare_user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertIn("workspace:view", result)

    def test_empty_resource_conditions_matches_all_resources(self) -> None:
        """Policy with resource_conditions=[] is vacuously true — matches every resource."""
        bare_ws = Workspace.objects.create(
            organization=self.org, name="Bare WS", slug="barews",
        )
        Policy.objects.create(
            organization=self.org, name="Empty resource policy",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=bare_ws, resource_type="workspace",
        )
        self.assertIn("workspace:view", result)

    def test_mixed_wildcard_identity_does_not_grant_global_access(self) -> None:
        """A policy with wildcard + extra identity condition must not match users lacking the extra attribute."""
        bare_user = User.objects.create_user(
            username="mixedwcuser", password="testpass", current_organization=self.org,
        )
        Policy.objects.create(
            organization=self.org, name="Mixed wildcard identity",
            resource_type="workspace",
            identity_conditions=[{"key": "*", "value": "*"}, {"key": "team", "value": "finance"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=bare_user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)

    def test_deny_on_admin_action_leaves_implied_actions(self) -> None:
        """Denying !workspace:admin must not strip the implied view+edit.

        Grant hierarchy expansion is one-way: grants expand downward but denials
        are literal. If denials were also expanded, a single !workspace:admin would
        nuke all workspace actions, which is not the intended semantics.
        """
        Policy.objects.create(
            organization=self.org, name="Admin grant",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:admin"],
        )
        Policy.objects.create(
            organization=self.org, name="Deny admin",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["!workspace:admin"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertNotIn("workspace:admin", result)
        self.assertIn("workspace:view", result)
        self.assertIn("workspace:edit", result)

    def test_deny_only_policy_grants_nothing(self) -> None:
        """A policy with only deny actions and no matching grants yields empty set."""
        Policy.objects.create(
            organization=self.org, name="Deny only",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["!workspace:view", "!workspace:edit"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertEqual(result, set())


# ---------------------------------------------------------------------------
# 1.5 Unscoped Evaluation
# ---------------------------------------------------------------------------


class TestEvaluatePoliciesUnscoped(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Unscoped Org", slug="unscoped-org")
        self.user = User.objects.create_user(
            username="unscopeduser", password="testpass", current_organization=self.org,
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="role", value="developer",
        )

    def test_wildcard_resource_policy_matches(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Create workspaces",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:edit"],
        )
        result = abac_service.evaluate_policies_unscoped(
            organization=self.org, user=self.user, resource_type="workspace",
        )
        self.assertIn("workspace:edit", result)

    def test_non_wildcard_resource_policy_ignored(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Specific workspace only",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies_unscoped(
            organization=self.org, user=self.user, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)

    def test_hierarchy_expansion_still_applies(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Admin all workspaces",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:admin"],
        )
        result = abac_service.evaluate_policies_unscoped(
            organization=self.org, user=self.user, resource_type="workspace",
        )
        self.assertEqual(result, {"workspace:admin", "workspace:view", "workspace:edit"})

    def test_empty_resource_conditions_does_not_match_unscoped(self) -> None:
        """Empty resource_conditions=[] is NOT treated as wildcard in unscoped evaluation.

        Unlike evaluate_policies where [] is vacuously true (matches all resources),
        evaluate_policies_unscoped requires an explicit wildcard [{"key":"*","value":"*"}].
        """
        Policy.objects.create(
            organization=self.org, name="Empty resource policy",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies_unscoped(
            organization=self.org, user=self.user, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)


# ---------------------------------------------------------------------------
# 1.6 Resource Filtering
# ---------------------------------------------------------------------------


class TestFilterPermittedResources(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Filter Org", slug="filter-org")
        self.user = User.objects.create_user(
            username="filteruser", password="testpass", current_organization=self.org,
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="role", value="developer",
        )
        self.ws_eng = Workspace.objects.create(
            organization=self.org, name="Engineering", slug="engineering",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.ws_eng,
            key="domain", value="engineering",
        )
        self.ws_fin = Workspace.objects.create(
            organization=self.org, name="Finance", slug="finance",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.ws_fin,
            key="domain", value="finance",
        )
        self.ws_hr = Workspace.objects.create(
            organization=self.org, name="HR", slug="hr",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.ws_hr,
            key="domain", value="hr",
        )

    def _all_workspaces(self) -> QuerySet[Workspace]:
        return Workspace.objects.filter(
            organization=self.org, pk__in=[self.ws_eng.pk, self.ws_fin.pk, self.ws_hr.pk],
        )

    def test_wildcard_resource_returns_entire_queryset(self) -> None:
        Policy.objects.create(
            organization=self.org, name="All workspaces",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:view"],
        )
        qs = self._all_workspaces()
        result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        self.assertEqual(set(result.values_list("pk", flat=True)), set(qs.values_list("pk", flat=True)))

    def test_tag_scoped_returns_only_matching(self) -> None:
        Policy.objects.create(
            organization=self.org, name="Engineering only",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        qs = self._all_workspaces()
        result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        self.assertEqual(set(result.values_list("pk", flat=True)), {self.ws_eng.pk})

    def test_no_matching_identity_returns_empty(self) -> None:
        bare_user = User.objects.create_user(
            username="noaccess", password="testpass", current_organization=self.org,
        )
        Policy.objects.create(
            organization=self.org, name="Devs only",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        qs = self._all_workspaces()
        result = abac_service.filter_permitted_resources(
            organization=self.org, user=bare_user,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        self.assertEqual(result.count(), 0)

    def test_wildcard_and_nonwildcard_mix(self) -> None:
        Policy.objects.create(
            organization=self.org, name="View all workspaces",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:view"],
        )
        Policy.objects.create(
            organization=self.org, name="Edit engineering only",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:edit"],
        )
        qs = self._all_workspaces()

        view_result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        self.assertEqual(
            set(view_result.values_list("pk", flat=True)),
            set(qs.values_list("pk", flat=True)),
        )

        edit_result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:edit",
        )
        self.assertEqual(set(edit_result.values_list("pk", flat=True)), {self.ws_eng.pk})

    def test_deny_removes_specific_resources(self) -> None:
        for domain in ["engineering", "finance", "hr"]:
            Policy.objects.create(
                organization=self.org, name=f"Grant {domain}",
                resource_type="workspace",
                identity_conditions=[{"key": "role", "value": "developer"}],
                resource_conditions=[{"key": "domain", "value": domain}],
                actions=["workspace:view"],
            )
        Policy.objects.create(
            organization=self.org, name="Deny finance",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "finance"}],
            actions=["!workspace:view"],
        )
        qs = self._all_workspaces()
        result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        result_pks = set(result.values_list("pk", flat=True))
        self.assertIn(self.ws_eng.pk, result_pks)
        self.assertIn(self.ws_hr.pk, result_pks)
        self.assertNotIn(self.ws_fin.pk, result_pks)

    def test_wildcard_grant_with_tag_scoped_deny(self) -> None:
        """Tag-scoped deny must override a wildcard grant for matching resources."""
        Policy.objects.create(
            organization=self.org, name="View all workspaces",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:view"],
        )
        Policy.objects.create(
            organization=self.org, name="Deny finance",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "finance"}],
            actions=["!workspace:view"],
        )
        qs = self._all_workspaces()
        result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        result_pks = set(result.values_list("pk", flat=True))
        self.assertIn(self.ws_eng.pk, result_pks)
        self.assertIn(self.ws_hr.pk, result_pks)
        self.assertNotIn(self.ws_fin.pk, result_pks)

    def test_hierarchy_from_scoped_grant_in_per_resource_path(self) -> None:
        """Scoped admin grant must expand to edit in the per-resource fallback path.

        Wildcard grants view on everything. Scoped grants admin on engineering only.
        Asking for edit: the wildcard shortcut only sees view (not edit), so it falls
        through to per-resource evaluation. The per-resource path must expand the
        scoped admin grant on engineering to include edit.
        """
        Policy.objects.create(
            organization=self.org, name="View all",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:view"],
        )
        Policy.objects.create(
            organization=self.org, name="Admin engineering",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:admin"],
        )
        qs = self._all_workspaces()
        result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:edit",
        )
        self.assertEqual(set(result.values_list("pk", flat=True)), {self.ws_eng.pk})

    def test_wildcard_deny_on_hierarchy_expanded_action(self) -> None:
        """Wildcard deny on an expanded action must block the shortcut for that action.

        Wildcard grants admin (expands to admin+view+edit), wildcard denies edit.
        Asking for view should take the shortcut (view is in expanded-denials).
        Asking for edit should fall through and be denied on all resources.
        """
        Policy.objects.create(
            organization=self.org, name="Admin all",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:admin"],
        )
        Policy.objects.create(
            organization=self.org, name="Deny edit globally",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["!workspace:edit"],
        )
        qs = self._all_workspaces()

        view_result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        self.assertEqual(
            set(view_result.values_list("pk", flat=True)),
            set(qs.values_list("pk", flat=True)),
        )

        edit_result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:edit",
        )
        self.assertEqual(edit_result.count(), 0)


# ---------------------------------------------------------------------------
# 1.6b Cross-Org Isolation
# ---------------------------------------------------------------------------


class TestCrossOrgIsolation(TestCase):
    """Verify ABAC engine rejects cross-org data even when present in the DB."""

    def setUp(self) -> None:
        self.org_a = Organization.objects.create(name="Org A", slug="org-a")
        self.org_b = Organization.objects.create(name="Org B", slug="org-b")
        self.user_a = User.objects.create_user(
            username="user_a", password="testpass", current_organization=self.org_a,
        )
        IdentityAttribute.objects.create(
            organization=self.org_a, user=self.user_a, key="role", value="developer",
        )
        self.ws_a = Workspace.objects.create(
            organization=self.org_a, name="WS-A", slug="ws-a",
        )
        ResourceTag.objects.create(
            organization=self.org_a, resource_type="workspace", workspace=self.ws_a,
            key="domain", value="engineering",
        )
        self.ws_b = Workspace.objects.create(
            organization=self.org_b, name="WS-B", slug="ws-b",
        )
        ResourceTag.objects.create(
            organization=self.org_b, resource_type="workspace", workspace=self.ws_b,
            key="domain", value="engineering",
        )

    def test_filter_permitted_raises_on_mixed_org_queryset(self) -> None:
        """Passing a queryset with foreign-org resources is a caller bug — must raise."""
        Policy.objects.create(
            organization=self.org_a, name="View all workspaces",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:view"],
        )
        mixed_qs = Workspace.objects.filter(pk__in=[self.ws_a.pk, self.ws_b.pk])
        with self.assertRaises(ValueError) as cm:
            abac_service.filter_permitted_resources(
                organization=self.org_a, user=self.user_a,
                queryset=mixed_qs, resource_type="workspace", action="workspace:view",
            )
        self.assertIn("outside organization", str(cm.exception))

    def test_filter_permitted_succeeds_with_properly_scoped_queryset(self) -> None:
        """A queryset scoped to the correct org works normally."""
        Policy.objects.create(
            organization=self.org_a, name="View all workspaces",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
            actions=["workspace:view"],
        )
        scoped_qs = Workspace.objects.filter(pk=self.ws_a.pk)
        result = abac_service.filter_permitted_resources(
            organization=self.org_a, user=self.user_a,
            queryset=scoped_qs, resource_type="workspace", action="workspace:view",
        )
        self.assertEqual(set(result.values_list("pk", flat=True)), {self.ws_a.pk})

    def test_evaluate_policies_ignores_foreign_org_policy(self) -> None:
        """A policy in org-B must not affect evaluation in org-A."""
        Policy.objects.create(
            organization=self.org_b, name="B grants view",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org_a, user=self.user_a,
            resource=self.ws_a, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)

    def test_malformed_tag_with_wrong_org_excluded_from_evaluation(self) -> None:
        """A ResourceTag with mismatched org must not influence policy evaluation."""
        ResourceTag.objects.create(
            organization=self.org_b, resource_type="workspace", workspace=self.ws_a,
            key="secret", value="granted",
        )
        Policy.objects.create(
            organization=self.org_a, name="Secret access",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "secret", "value": "granted"}],
            actions=["workspace:admin"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org_a, user=self.user_a,
            resource=self.ws_a, resource_type="workspace",
        )
        self.assertNotIn("workspace:admin", result)

    def test_evaluate_policies_raises_on_resource_org_mismatch(self) -> None:
        """Passing a resource from a different org is a caller bug — must raise."""
        with self.assertRaises(ValueError) as cm:
            abac_service.evaluate_policies(
                organization=self.org_a, user=self.user_a,
                resource=self.ws_b, resource_type="workspace",
            )
        self.assertIn("belongs to organization", str(cm.exception))

    def test_check_action_raises_on_resource_org_mismatch(self) -> None:
        """check_action inherits the resource-org assertion from evaluate_policies."""
        with self.assertRaises(ValueError):
            abac_service.check_action(
                organization=self.org_a, user=self.user_a,
                resource=self.ws_b, resource_type="workspace", action="workspace:view",
            )

    def test_get_effective_tags_raises_on_resource_org_mismatch(self) -> None:
        """get_effective_tags rejects a resource from a foreign org."""
        with self.assertRaises(ValueError):
            abac_service.get_effective_tags(
                organization=self.org_a, resource=self.ws_b, resource_type="workspace",
            )


# ---------------------------------------------------------------------------
# 4. Cross-Organization Isolation
# ---------------------------------------------------------------------------


class TestCrossOrgIsolationEndToEnd(TestCase):
    """Section 4: Full cross-org isolation scenarios from the test plan.

    Lower-level cross-org assertions live in TestCrossOrgIsolation (1.6b) and
    TestCrossOrgAttributeIsolation (1.7b). These tests exercise the full engine
    chain: bootstrapping, attributes, policies, and evaluation across two orgs.
    """

    def setUp(self) -> None:
        self.org_a = Organization.objects.create(name="Org A", slug="cross-org-a")
        self.org_b = Organization.objects.create(name="Org B", slug="cross-org-b")

        self.user = User.objects.create_user(
            username="cross_org_user", password="testpass", current_organization=self.org_a,
        )
        abac_service.bootstrap_organization(organization=self.org_a, admin_user=self.user)

        self.ws_b = Workspace.objects.create(organization=self.org_b, name="WS-B", slug="ws-b")
        ResourceTag.objects.create(
            organization=self.org_b, resource_type="workspace", workspace=self.ws_b,
            key="domain", value="engineering",
        )

    def test_org_a_admin_gets_no_access_evaluating_org_b_workspace(self) -> None:
        """Fully bootstrapped admin in org A gets empty set when evaluating org B's workspace."""
        self.assertTrue(abac_service.is_org_admin(organization=self.org_a, user=self.user))

        result = abac_service.evaluate_policies(
            organization=self.org_b, user=self.user,
            resource=self.ws_b, resource_type="workspace",
        )
        self.assertEqual(result, set())

    def test_org_a_policies_do_not_influence_org_b_evaluation(self) -> None:
        """Policy in org A granting access to matching tags has no effect in org B."""
        Policy.objects.create(
            organization=self.org_a, name="Eng access",
            resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        IdentityAttribute.objects.create(
            organization=self.org_a, user=self.user, key="role", value="developer",
        )

        result = abac_service.evaluate_policies(
            organization=self.org_b, user=self.user,
            resource=self.ws_b, resource_type="workspace",
        )
        self.assertEqual(result, set())

    def test_filter_permitted_resources_returns_empty_for_cross_org_user(self) -> None:
        """filter_permitted_resources with properly-scoped org B queryset returns nothing for org A admin."""
        self.assertTrue(abac_service.is_org_admin(organization=self.org_a, user=self.user))

        org_b_qs = Workspace.objects.filter(organization=self.org_b)
        result = abac_service.filter_permitted_resources(
            organization=self.org_b, user=self.user,
            queryset=org_b_qs, resource_type="workspace", action="workspace:view",
        )
        self.assertEqual(result.count(), 0)


# ---------------------------------------------------------------------------
# 1.7 Org Admin Check
# ---------------------------------------------------------------------------


class TestIsOrgAdmin(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Admin Org", slug="admin-org")
        self.user = User.objects.create_user(
            username="adminuser", password="testpass", current_organization=self.org,
        )

    def test_direct_admin_attribute(self) -> None:
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="org-role", value="admin",
        )
        self.assertTrue(abac_service.is_org_admin(organization=self.org, user=self.user))

    def test_group_inherited_admin(self) -> None:
        group = Group.objects.create(organization=self.org, name="Admins")
        GroupMembership.objects.create(group=group, user=self.user)
        GroupAttribute.objects.create(group=group, key="org-role", value="admin")
        self.assertTrue(abac_service.is_org_admin(organization=self.org, user=self.user))

    def test_no_admin_attribute(self) -> None:
        self.assertFalse(abac_service.is_org_admin(organization=self.org, user=self.user))

    def test_admin_in_one_org_not_admin_in_another(self) -> None:
        """org-role=admin in org A must not leak into org B."""
        org_b = Organization.objects.create(name="Other Org", slug="other-org")
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="org-role", value="admin",
        )
        self.assertTrue(abac_service.is_org_admin(organization=self.org, user=self.user))
        self.assertFalse(abac_service.is_org_admin(organization=org_b, user=self.user))


# ---------------------------------------------------------------------------
# 1.7b Cross-Org Attribute Isolation
# ---------------------------------------------------------------------------


class TestCrossOrgAttributeIsolation(TestCase):
    """Attributes from one org must never appear when querying another."""

    def setUp(self) -> None:
        self.org_a = Organization.objects.create(name="Attr Org A", slug="attr-org-a")
        self.org_b = Organization.objects.create(name="Attr Org B", slug="attr-org-b")
        self.user = User.objects.create_user(
            username="cross_attr_user", password="testpass", current_organization=self.org_a,
        )

    def test_direct_attribute_scoped_to_org(self) -> None:
        IdentityAttribute.objects.create(
            organization=self.org_a, user=self.user, key="role", value="developer",
        )
        attrs_a = abac_service.get_effective_attributes(organization=self.org_a, user=self.user)
        attrs_b = abac_service.get_effective_attributes(organization=self.org_b, user=self.user)

        self.assertIn(("role", "developer", "direct"), attrs_a)
        self.assertNotIn(("role", "developer", "direct"), attrs_b)

    def test_group_attribute_scoped_to_org(self) -> None:
        group = Group.objects.create(organization=self.org_a, name="Engineers")
        GroupMembership.objects.create(group=group, user=self.user)
        GroupAttribute.objects.create(group=group, key="team", value="backend")

        attrs_a = abac_service.get_effective_attributes(organization=self.org_a, user=self.user)
        attrs_b = abac_service.get_effective_attributes(organization=self.org_b, user=self.user)

        self.assertIn(("team", "backend", "group:Engineers"), attrs_a)
        self.assertNotIn(("team", "backend", "group:Engineers"), attrs_b)

    def test_system_attribute_present_in_both_orgs(self) -> None:
        """authenticated=true is context-free and appears regardless of org."""
        attrs_a = abac_service.get_effective_attributes(organization=self.org_a, user=self.user)
        attrs_b = abac_service.get_effective_attributes(organization=self.org_b, user=self.user)

        self.assertIn(("authenticated", "true", "system"), attrs_a)
        self.assertIn(("authenticated", "true", "system"), attrs_b)


# ---------------------------------------------------------------------------
# 3.1 Organization Bootstrap
# ---------------------------------------------------------------------------


class TestBootstrapOrganization(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Bootstrap Org", slug="bootstrap-org")
        self.admin_user = User.objects.create_user(
            username="bootstrap_admin", password="testpass", current_organization=self.org,
        )

    def test_creates_admin_attribute(self) -> None:
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        self.assertTrue(
            IdentityAttribute.objects.filter(
                organization=self.org, user=self.admin_user, key="org-role", value="admin",
            ).exists()
        )

    def test_creates_expected_seed_policies(self) -> None:
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        seed_policies = Policy.objects.filter(organization=self.org, is_system=True)
        # 3 admin (ws/env/app) + 2 member (ws/env) + 2 viewer (ws/env) + 1 PA owner
        # + 3 shared-credential (everyone/user/workspace).
        self.assertEqual(seed_policies.count(), 11)

        resource_types = set(seed_policies.values_list("resource_type", flat=True))
        self.assertEqual(resource_types, {"workspace", "environment", "app", "credential"})

    def test_seed_policy_actions_admin(self) -> None:
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        admin_condition = [{"key": "org-role", "value": "admin"}]
        ws = Policy.objects.get(organization=self.org, identity_conditions=admin_condition, resource_type="workspace")
        env = Policy.objects.get(organization=self.org, identity_conditions=admin_condition, resource_type="environment")
        app = Policy.objects.get(organization=self.org, identity_conditions=admin_condition, resource_type="app")
        self.assertEqual(ws.actions, ["workspace:admin"])
        self.assertEqual(env.actions, ["environment:admin"])
        self.assertEqual(app.actions, ["app:use"])

    def test_seed_policy_actions_member(self) -> None:
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        member_condition = [{"key": "org-role", "value": "member"}]
        ws = Policy.objects.get(organization=self.org, identity_conditions=member_condition, resource_type="workspace")
        env = Policy.objects.get(organization=self.org, identity_conditions=member_condition, resource_type="environment")
        self.assertEqual(ws.actions, ["workspace:view", "workspace:edit"])
        self.assertEqual(env.actions, ["environment:view", "environment:deploy"])
        # Members do not get a wildcard app:use grant — per-app policies govern
        # app access (see create_default_app_policy).
        self.assertFalse(
            Policy.objects.filter(
                organization=self.org, identity_conditions=member_condition, resource_type="app",
            ).exists(),
        )

    def test_seed_policy_actions_viewer(self) -> None:
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        viewer_condition = [{"key": "org-role", "value": "viewer"}]
        ws = Policy.objects.get(organization=self.org, identity_conditions=viewer_condition, resource_type="workspace")
        env = Policy.objects.get(organization=self.org, identity_conditions=viewer_condition, resource_type="environment")
        self.assertEqual(ws.actions, ["workspace:view"])
        self.assertEqual(env.actions, ["environment:view"])
        self.assertFalse(
            Policy.objects.filter(
                organization=self.org, identity_conditions=viewer_condition, resource_type="app",
            ).exists(),
        )

    def test_pa_owner_policy_is_seeded(self) -> None:
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        pa = Policy.objects.get(
            organization=self.org, name="Personal Assistant: owner access",
        )
        self.assertEqual(pa.resource_type, "app")
        self.assertEqual(pa.identity_conditions, [{"key": "username", "value": "$resource.owner"}])
        self.assertEqual(pa.resource_conditions, [{"key": "app-type", "value": "personal-assistant"}])
        self.assertEqual(pa.actions, ["app:use"])

    def test_idempotent(self) -> None:
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

        self.assertEqual(
            IdentityAttribute.objects.filter(
                organization=self.org, user=self.admin_user, key="org-role", value="admin",
            ).count(),
            1,
        )
        self.assertEqual(
            Policy.objects.filter(organization=self.org, is_system=True).count(),
            11,
        )


# ---------------------------------------------------------------------------
# 3.2 App Default Policy
# ---------------------------------------------------------------------------


class TestCreateDefaultAppPolicy(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="AppDefault Org", slug="appdefault-org")
        self.workspace = Workspace.objects.get(organization=self.org, slug="default")
        self.aws_account = AWSAccount.objects.create(
            organization=self.org, name="Test Account",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Default", slug="default", aws_region="us-east-1",
        )

    def _make_app(self, name: str, slug: str) -> App:
        return App.objects.create(
            organization=self.org, workspace=self.workspace,
            source_template=app_test_factories.make_source_template(),
            environment=self.env, name=name, slug=slug,
            container_port=8000, health_check_path="/health",
            cpu=256, memory=512,
        )

    def test_creates_app_name_tag(self) -> None:
        app = self._make_app(name="My Dashboard", slug="my-dashboard")
        self.assertTrue(
            ResourceTag.objects.filter(
                organization=self.org, resource_type="app", app=app,
                key="app-name", value="my-dashboard",
            ).exists()
        )

    def test_creates_wildcard_app_use_policy(self) -> None:
        app = self._make_app(name="My Dashboard", slug="my-dashboard")
        policy = Policy.objects.get(
            organization=self.org, resource_type="app",
            name=f"Default: {app.name} open access",
        )
        self.assertEqual(policy.identity_conditions, [{"key": "*", "value": "*"}])
        self.assertEqual(policy.resource_conditions, [{"key": "app-name", "value": "my-dashboard"}])
        self.assertEqual(policy.actions, ["app:use"])
        self.assertTrue(policy.is_system)

    def test_idempotent(self) -> None:
        app = self._make_app(name="My Dashboard", slug="my-dashboard")
        abac_service.create_default_app_policy(app)

        self.assertEqual(
            ResourceTag.objects.filter(
                organization=self.org, resource_type="app", app=app,
                key="app-name", value="my-dashboard",
            ).count(),
            1,
        )
        self.assertEqual(
            Policy.objects.filter(
                organization=self.org, resource_type="app",
                name=f"Default: {app.name} open access",
            ).count(),
            1,
        )

    def test_policy_proxy_template_skips_open_access_policy(self) -> None:
        # PAs (and any policy-proxy'd app) must not get the wildcard open-access grant —
        # their access is governed by the global Personal Assistant owner policy.
        template = AppTemplate.objects.create(
            name="PA", slug="pa-fixture", description="", icon="x", category="x",
            cpu=256, memory=512,
            alb_target_container="policy-proxy",
            containers=[
                {
                    "name": "app",
                    "image_source": "template",
                    "template_path": "hermes_agent",
                    "container_port": 8000,
                    "health_check_path": "/health",
                    "configurable_variables": [],
                },
                {
                    "name": "policy-proxy",
                    "image_source": "template",
                    "template_path": "policy_proxy",
                    "role": "policy_proxy",
                    "upstream_container": "app",
                    "container_port": 8001,
                    "configurable_variables": [],
                },
            ],
            is_active=True,
        )
        app = App.objects.create(
            organization=self.org, workspace=self.workspace,
            environment=self.env, name="Vmendi PA", slug="vmendi-pa",
            container_port=8000,
            health_check_path="/health", source_template=template,
            cpu=256, memory=512,
        )
        # app-name tag still created (used elsewhere for visibility).
        self.assertTrue(
            ResourceTag.objects.filter(
                organization=self.org, resource_type="app", app=app,
                key="app-name", value="vmendi-pa",
            ).exists()
        )
        # But no open-access policy.
        self.assertFalse(
            Policy.objects.filter(
                organization=self.org, name=f"Default: {app.name} open access",
            ).exists(),
        )


# ---------------------------------------------------------------------------
# 3.3 Auto-Tags via Signals
# ---------------------------------------------------------------------------


class TestAutoTagSignals(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Signal Org", slug="signal-org")
        self.aws_account = AWSAccount.objects.create(
            organization=self.org, name="Test Account",
        )

    def test_workspace_creation_produces_name_tag(self) -> None:
        ws = Workspace.objects.create(
            organization=self.org, name="Data Platform", slug="data-platform",
        )
        self.assertTrue(
            ResourceTag.objects.filter(
                organization=self.org, resource_type="workspace", workspace=ws,
                key="workspace-name", value="data-platform",
            ).exists()
        )

    def test_default_workspace_gets_name_tag(self) -> None:
        """The auto-created 'Default' workspace from the Organization signal also gets tagged."""
        default_ws = Workspace.objects.get(organization=self.org, slug="default")
        self.assertTrue(
            ResourceTag.objects.filter(
                organization=self.org, resource_type="workspace", workspace=default_ws,
                key="workspace-name", value="default",
            ).exists()
        )

    def test_environment_creation_produces_name_tag(self) -> None:
        env = Environment.objects.create(
            aws_account=self.aws_account, name="Production", slug="production",
            aws_region="us-east-1",
        )
        self.assertTrue(
            ResourceTag.objects.filter(
                organization=self.org, resource_type="environment", environment=env,
                key="environment-name", value="production",
            ).exists()
        )


# ---------------------------------------------------------------------------
# 5.1 Team Onboarding
# ---------------------------------------------------------------------------


class TestTeamOnboardingScenario(TestCase):
    """Replicate the 'onboarding a new team' flow from authorization_design_abac.md.

    1. Create org, bootstrap, create a group with team=data-platform
    2. Add users to the group (they inherit the attribute)
    3. Create workspace tagged domain=data-platform
    4. Create policy: IF identity team=data-platform AND resource domain=data-platform THEN workspace:view, workspace:edit
    5. Verify group members can access the workspace
    6. Verify a user outside the group cannot access the workspace
    7. Add a new user to the group — they gain access without any policy change
    """

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Onboarding Org", slug="onboarding-org")
        self.admin = User.objects.create_user(
            username="onboard_admin", password="testpass", current_organization=self.org,
        )
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin)

        self.group = Group.objects.create(organization=self.org, name="Data Platform Team")
        GroupAttribute.objects.create(group=self.group, key="team", value="data-platform")

        self.alice = User.objects.create_user(
            username="onboard_alice", password="testpass", current_organization=self.org,
        )
        self.bob = User.objects.create_user(
            username="onboard_bob", password="testpass", current_organization=self.org,
        )
        GroupMembership.objects.create(group=self.group, user=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.bob)

        self.workspace = Workspace.objects.create(
            organization=self.org, name="Data Platform", slug="data-platform",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="data-platform",
        )

        Policy.objects.create(
            organization=self.org, name="Data team workspace access",
            resource_type="workspace",
            identity_conditions=[{"key": "team", "value": "data-platform"}],
            resource_conditions=[{"key": "domain", "value": "data-platform"}],
            actions=["workspace:view", "workspace:edit"],
        )

        self.outsider = User.objects.create_user(
            username="onboard_outsider", password="testpass", current_organization=self.org,
        )

    def test_group_members_can_view_workspace(self) -> None:
        for user in [self.alice, self.bob]:
            result = abac_service.evaluate_policies(
                organization=self.org, user=user,
                resource=self.workspace, resource_type="workspace",
            )
            self.assertIn("workspace:view", result, f"{user.username} should have workspace:view")
            self.assertIn("workspace:edit", result, f"{user.username} should have workspace:edit")

    def test_outsider_cannot_access_workspace(self) -> None:
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.outsider,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)
        self.assertNotIn("workspace:edit", result)

    def test_outsider_excluded_from_filtered_queryset(self) -> None:
        qs = Workspace.objects.filter(pk=self.workspace.pk)
        result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.outsider,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        self.assertEqual(result.count(), 0)

    def test_new_member_gains_access_without_policy_change(self) -> None:
        carol = User.objects.create_user(
            username="onboard_carol", password="testpass", current_organization=self.org,
        )
        result_before = abac_service.evaluate_policies(
            organization=self.org, user=carol,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result_before)

        GroupMembership.objects.create(group=self.group, user=carol)

        result_after = abac_service.evaluate_policies(
            organization=self.org, user=carol,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertIn("workspace:view", result_after)
        self.assertIn("workspace:edit", result_after)

    def test_filter_returns_workspace_for_members_only(self) -> None:
        qs = Workspace.objects.filter(pk=self.workspace.pk)
        for user in [self.alice, self.bob]:
            result = abac_service.filter_permitted_resources(
                organization=self.org, user=user,
                queryset=qs, resource_type="workspace", action="workspace:view",
            )
            self.assertIn(
                self.workspace.pk,
                set(result.values_list("pk", flat=True)),
                f"{user.username} should see workspace in filtered results",
            )


# ---------------------------------------------------------------------------
# 5.2 Deny-Override Scenario
# ---------------------------------------------------------------------------


class TestDenyOverrideScenario(TestCase):
    """Contractor-developer deny interaction from the design doc.

    1. Grant environment:deploy to job-function=developer on tier=staging
    2. Deny !environment:deploy to employment-type=contractor on tier=staging
    3. A developer-contractor (both attributes) is denied despite the grant
    4. A developer-employee (only job-function=developer) can deploy normally
    """

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Deny Org", slug="deny-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Deny Account")

        self.staging = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging",
            aws_region="us-east-1",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=self.staging,
            key="tier", value="staging",
        )

        Policy.objects.create(
            organization=self.org, name="Developers can deploy to staging",
            resource_type="environment",
            identity_conditions=[{"key": "job-function", "value": "developer"}],
            resource_conditions=[{"key": "tier", "value": "staging"}],
            actions=["environment:deploy"],
        )
        Policy.objects.create(
            organization=self.org, name="No contractor deploys to staging",
            resource_type="environment",
            identity_conditions=[{"key": "employment-type", "value": "contractor"}],
            resource_conditions=[{"key": "tier", "value": "staging"}],
            actions=["!environment:deploy"],
        )

        self.contractor_dev = User.objects.create_user(
            username="deny_contractor_dev", password="testpass", current_organization=self.org,
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.contractor_dev,
            key="job-function", value="developer",
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.contractor_dev,
            key="employment-type", value="contractor",
        )

        self.employee_dev = User.objects.create_user(
            username="deny_employee_dev", password="testpass", current_organization=self.org,
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.employee_dev,
            key="job-function", value="developer",
        )

    def test_contractor_developer_denied_deploy(self) -> None:
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.contractor_dev,
            resource=self.staging, resource_type="environment",
        )
        self.assertNotIn("environment:deploy", result)

    def test_employee_developer_can_deploy(self) -> None:
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.employee_dev,
            resource=self.staging, resource_type="environment",
        )
        self.assertIn("environment:deploy", result)

    def test_check_action_reflects_deny_override(self) -> None:
        self.assertFalse(abac_service.check_action(
            organization=self.org, user=self.contractor_dev,
            resource=self.staging, resource_type="environment", action="environment:deploy",
        ))
        self.assertTrue(abac_service.check_action(
            organization=self.org, user=self.employee_dev,
            resource=self.staging, resource_type="environment", action="environment:deploy",
        ))

    def test_filter_excludes_staging_for_contractor(self) -> None:
        qs = Environment.objects.filter(pk=self.staging.pk)
        result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.contractor_dev,
            queryset=qs, resource_type="environment", action="environment:deploy",
        )
        self.assertEqual(result.count(), 0)

    def test_filter_includes_staging_for_employee(self) -> None:
        qs = Environment.objects.filter(pk=self.staging.pk)
        result = abac_service.filter_permitted_resources(
            organization=self.org, user=self.employee_dev,
            queryset=qs, resource_type="environment", action="environment:deploy",
        )
        self.assertEqual(set(result.values_list("pk", flat=True)), {self.staging.pk})


# ---------------------------------------------------------------------------
# 5.3 Tag Inheritance Consistency
# ---------------------------------------------------------------------------


class TestTagInheritanceConsistency(TestCase):
    """Workspace tag removal propagates to app access via tag inheritance.

    1. Create a workspace with tag domain=finance
    2. Create an app in that workspace — app inherits domain=finance
    3. Create a policy granting app:use when resource has domain=finance
    4. Verify the app is accessible
    5. Remove the domain=finance tag from the workspace
    6. Verify the app is no longer accessible via that policy
    """

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="TagInherit Org", slug="taginherit-org")
        self.workspace = Workspace.objects.create(
            organization=self.org, name="Finance", slug="finance",
        )
        self.ws_tag = ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="finance",
        )
        self.aws_account = AWSAccount.objects.create(
            organization=self.org, name="Test Account",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Default", slug="default", aws_region="us-east-1",
        )
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace,
            source_template=app_test_factories.make_source_template(),
            environment=self.env, name="FinReports", slug="finreports",
            container_port=8000,
            health_check_path="/health", cpu=256, memory=512,
        )

        # Remove the auto-created open-access policy from post_save signal
        # so this test isolates the domain=finance tag inheritance pathway.
        Policy.objects.filter(
            organization=self.org, name=f"Default: {self.app.name} open access",
        ).delete()

        self.user = User.objects.create_user(
            username="taginherit_user", password="testpass", current_organization=self.org,
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user,
            key="department", value="finance",
        )

        Policy.objects.create(
            organization=self.org, name="Finance apps",
            resource_type="app",
            identity_conditions=[{"key": "department", "value": "finance"}],
            resource_conditions=[{"key": "domain", "value": "finance"}],
            actions=["app:use"],
        )

    def test_app_inherits_workspace_tag(self) -> None:
        tags = abac_service.get_effective_tags(
            organization=self.org, resource=self.app, resource_type="app",
        )
        inherited_keys = {k for k, _, s in tags if s.startswith("inherited:")}
        self.assertIn("domain", inherited_keys)

    def test_app_accessible_via_inherited_tag(self) -> None:
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.app, resource_type="app",
        )
        self.assertIn("app:use", result)

    def test_removing_workspace_tag_revokes_app_access(self) -> None:
        self.assertIn("app:use", abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.app, resource_type="app",
        ))

        self.ws_tag.delete()

        tags_after = abac_service.get_effective_tags(
            organization=self.org, resource=self.app, resource_type="app",
        )
        inherited_domain = [(k, v) for k, v, s in tags_after if k == "domain" and s.startswith("inherited:")]
        self.assertEqual(inherited_domain, [])

        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.app, resource_type="app",
        )
        self.assertNotIn("app:use", result)

    def test_direct_app_tag_unaffected_by_workspace_tag_removal(self) -> None:
        """An app with its own domain=finance tag keeps access after workspace tag removal."""
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=self.app,
            key="domain", value="finance",
        )

        self.ws_tag.delete()

        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.app, resource_type="app",
        )
        self.assertIn("app:use", result)

    def test_filter_reflects_tag_inheritance_change(self) -> None:
        qs = App.objects.filter(pk=self.app.pk)

        result_before = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="app", action="app:use",
        )
        self.assertEqual(set(result_before.values_list("pk", flat=True)), {self.app.pk})

        self.ws_tag.delete()

        result_after = abac_service.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="app", action="app:use",
        )
        self.assertEqual(result_after.count(), 0)


# ---------------------------------------------------------------------------
# 6. Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Edge Org", slug="edge-org")
        self.user = User.objects.create_user(
            username="edgeuser", password="testpass", current_organization=self.org,
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="team", value="frontend",
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="team", value="backend",
        )
        self.workspace = Workspace.objects.create(
            organization=self.org, name="EdgeWS", slug="edgews",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="engineering",
        )

    def test_empty_actions_grants_nothing(self) -> None:
        """Policy with actions=[] should grant nothing even when conditions match."""
        Policy.objects.create(
            organization=self.org, name="Empty actions policy",
            resource_type="workspace",
            identity_conditions=[{"key": "team", "value": "frontend"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=[],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertEqual(result, set())

    def test_multi_value_same_key_policy_matches_one_value(self) -> None:
        """User with team=frontend AND team=backend — a policy requiring team=frontend matches."""
        Policy.objects.create(
            organization=self.org, name="Frontend team access",
            resource_type="workspace",
            identity_conditions=[{"key": "team", "value": "frontend"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertIn("workspace:view", result)

    def test_multi_value_same_key_policy_matches_other_value(self) -> None:
        """Same multi-value user — a policy requiring team=backend also matches."""
        Policy.objects.create(
            organization=self.org, name="Backend team access",
            resource_type="workspace",
            identity_conditions=[{"key": "team", "value": "backend"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:edit"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertIn("workspace:edit", result)

    def test_multi_value_same_key_no_match_for_absent_value(self) -> None:
        """User with team=frontend and team=backend does NOT match team=security."""
        Policy.objects.create(
            organization=self.org, name="Security team access",
            resource_type="workspace",
            identity_conditions=[{"key": "team", "value": "security"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        result = abac_service.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)


# ---------------------------------------------------------------------------
# Suggestion palette
# ---------------------------------------------------------------------------


class TestSuggestionPalette(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Suggest Org", slug="suggest-org")
        self.user = User.objects.create_user(username="suggest_user", password="testpass", current_organization=self.org)

    def test_identity_palette_keys_included(self) -> None:
        keys, _ = abac_service.get_identity_attribute_suggestions(self.org)
        for expected in abac_service.IDENTITY_SUGGESTIONS:
            self.assertIn(expected, keys)

    def test_identity_palette_pairs_included(self) -> None:
        _, pairs = abac_service.get_identity_attribute_suggestions(self.org)
        expected_keys, expected_pairs = abac_service._expand_suggestions(abac_service.IDENTITY_SUGGESTIONS)
        for expected_pair in expected_pairs:
            self.assertIn(expected_pair, pairs)

    def test_system_attributes_excluded_by_default(self) -> None:
        keys, pairs = abac_service.get_identity_attribute_suggestions(self.org)
        self.assertNotIn("authenticated", keys)
        self.assertNotIn(("authenticated", "true"), pairs)

    def test_system_attributes_included_with_flag(self) -> None:
        keys, pairs = abac_service.get_identity_attribute_suggestions(self.org, include_system=True)
        self.assertIn("authenticated", keys)
        self.assertIn(("authenticated", "true"), pairs)

    def test_identity_includes_attribute_keys(self) -> None:
        IdentityAttribute.objects.create(organization=self.org, user=self.user, key="custom-key", value="x")
        keys, pairs = abac_service.get_identity_attribute_suggestions(self.org)
        self.assertIn("custom-key", keys)
        self.assertIn(("custom-key", "x"), pairs)

    def test_identity_excludes_resource_tag_keys(self) -> None:
        workspace = Workspace.objects.get(organization=self.org, slug="default")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=workspace, key="infra-key", value="x",
        )
        keys, _ = abac_service.get_identity_attribute_suggestions(self.org)
        self.assertNotIn("infra-key", keys)

    def test_tag_includes_resource_tag_keys_for_matching_type(self) -> None:
        workspace = Workspace.objects.get(organization=self.org, slug="default")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=workspace, key="infra-key", value="x",
        )
        keys, pairs = abac_service.get_resource_tag_suggestions(self.org, "workspace")
        self.assertIn("infra-key", keys)
        self.assertIn(("infra-key", "x"), pairs)

    def test_tag_excludes_resource_tag_keys_for_other_type(self) -> None:
        workspace = Workspace.objects.get(organization=self.org, slug="default")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=workspace, key="infra-key", value="x",
        )
        keys, pairs = abac_service.get_resource_tag_suggestions(self.org, "environment")
        self.assertNotIn("infra-key", keys)
        self.assertNotIn(("infra-key", "x"), pairs)

    def test_tag_no_type_returns_all(self) -> None:
        workspace = Workspace.objects.get(organization=self.org, slug="default")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=workspace, key="infra-key", value="x",
        )
        keys, pairs = abac_service.get_resource_tag_suggestions(self.org)
        self.assertIn("infra-key", keys)
        self.assertIn(("infra-key", "x"), pairs)

    def test_tag_per_type_seed_suggestions(self) -> None:
        keys, pairs = abac_service.get_resource_tag_suggestions(self.org, "environment")
        self.assertIn("stage", keys)
        self.assertIn(("stage", "production"), pairs)
        self.assertNotIn("project", keys)

    def test_tag_excludes_identity_attribute_keys(self) -> None:
        IdentityAttribute.objects.create(organization=self.org, user=self.user, key="custom-key", value="x")
        keys, _ = abac_service.get_resource_tag_suggestions(self.org)
        self.assertNotIn("custom-key", keys)

    def test_results_are_sorted(self) -> None:
        keys, pairs = abac_service.get_identity_attribute_suggestions(self.org)
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(pairs, sorted(pairs))

    def test_by_type_tags_key_provenance(self) -> None:
        workspace = Workspace.objects.get(organization=self.org, slug="default")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=workspace, key="infra-key", value="x",
        )
        keys, _ = abac_service.get_resource_tag_suggestions_by_type(self.org)
        keys_map = dict(keys)
        # A workspace DB tag is attributed to the workspace type only.
        self.assertEqual(keys_map["infra-key"], "workspace")
        # 'stage' is an environment-only seed; 'sharing-scope' a credential-only seed.
        self.assertEqual(keys_map["stage"], "environment")
        self.assertEqual(keys_map["sharing-scope"], "credential")

    def test_by_type_tags_value_provenance(self) -> None:
        keys, pairs = abac_service.get_resource_tag_suggestions_by_type(self.org)
        pair_map = {(k, v): t for k, v, t in pairs}
        self.assertEqual(pair_map[("stage", "production")], "environment")
        self.assertEqual(pair_map[("sharing-scope", "everyone")], "credential")

    def test_by_type_shared_key_lists_all_types(self) -> None:
        # 'stage'='production' is an environment seed; adding it as a workspace
        # DB tag makes the key/pair span two types, joined in the provenance CSV.
        workspace = Workspace.objects.get(organization=self.org, slug="default")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=workspace, key="stage", value="production",
        )
        keys, pairs = abac_service.get_resource_tag_suggestions_by_type(self.org)
        self.assertEqual(dict(keys)["stage"], "environment,workspace")
        pair_map = {(k, v): t for k, v, t in pairs}
        self.assertEqual(pair_map[("stage", "production")], "environment,workspace")

    def test_by_type_results_are_sorted(self) -> None:
        keys, pairs = abac_service.get_resource_tag_suggestions_by_type(self.org)
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(pairs, sorted(pairs))

    def test_by_type_offers_app_workspace_reference(self) -> None:
        # The policy editor should suggest the $app cross-reference as the value
        # for shared-with-workspace (the canonical workspace-access condition).
        _, pairs = abac_service.get_resource_tag_suggestions_by_type(self.org)
        pair_map = {(k, v): t for k, v, t in pairs}
        self.assertEqual(pair_map[("shared-with-workspace", "$app.workspace-name")], "credential")

    def test_tag_palette_excludes_policy_reference(self) -> None:
        # The cross-reference must NOT leak into the per-resource tag editor.
        _, pairs = abac_service.get_resource_tag_suggestions(self.org, "credential")
        self.assertNotIn(("shared-with-workspace", "$app.workspace-name"), pairs)

    def test_identity_by_type_offers_resource_reference(self) -> None:
        # The identity-condition value field should suggest $resource.shared-with-user
        # for the username key, gated to credential policies.
        _, pairs = abac_service.get_identity_attribute_suggestions_by_type(self.org)
        pair_map = {(k, v): t for k, v, t in pairs}
        self.assertEqual(pair_map[("username", "$resource.shared-with-user")], "credential")

    def test_identity_by_type_normal_attrs_apply_to_all_types(self) -> None:
        # Ordinary identity attributes are not type-specific, so they are tagged
        # with every resource type and always show.
        keys, _ = abac_service.get_identity_attribute_suggestions_by_type(self.org)
        self.assertEqual(dict(keys)["org-role"], "app,credential,environment,workspace")

    def test_identity_by_type_results_are_sorted(self) -> None:
        keys, pairs = abac_service.get_identity_attribute_suggestions_by_type(self.org)
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(pairs, sorted(pairs))

    def test_identity_by_type_offers_app_owner_reference(self) -> None:
        # Symmetric to the credential case: the PA owner policy matches
        # username = $resource.owner, gated to app policies.
        _, pairs = abac_service.get_identity_attribute_suggestions_by_type(self.org)
        pair_map = {(k, v): t for k, v, t in pairs}
        self.assertEqual(pair_map[("username", "$resource.owner")], "app")

    def test_app_resource_seed_suggestions(self) -> None:
        keys, pairs = abac_service.get_resource_tag_suggestions(self.org, "app")
        self.assertIn("app-type", keys)
        self.assertIn(("app-type", "personal-assistant"), pairs)
        self.assertIn("owner", keys)


# ---------------------------------------------------------------------------
# Self-referential conditions ($identity.<key> / $resource.<key>)
# ---------------------------------------------------------------------------


class TestSelfReferentialValidation(TestCase):

    def test_resource_ref_in_identity_value_is_valid(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "username", "value": "$resource.owner"}],
            resource_conditions=[{"key": "app-type", "value": "personal-assistant"}],
        )

    def test_identity_ref_in_resource_value_is_valid(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "username", "value": "alice"}],
            resource_conditions=[{"key": "owner", "value": "$identity.username"}],
        )

    def test_identity_ref_in_identity_value_raises(self) -> None:
        with self.assertRaises(ValidationError) as cm:
            abac_service.validate_policy_conditions(
                identity_conditions=[{"key": "username", "value": "$identity.email"}],
                resource_conditions=[],
            )
        self.assertIn("Identity", str(cm.exception))

    def test_resource_ref_in_resource_value_raises(self) -> None:
        with self.assertRaises(ValidationError) as cm:
            abac_service.validate_policy_conditions(
                identity_conditions=[],
                resource_conditions=[{"key": "owner", "value": "$resource.other"}],
            )
        self.assertIn("Resource", str(cm.exception))

    def test_reference_in_key_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            abac_service.validate_policy_conditions(
                identity_conditions=[{"key": "$resource.owner", "value": "alice"}],
                resource_conditions=[],
            )


class TestSelfReferentialEvaluation(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Owner Org", slug="owner-org")
        self.workspace = Workspace.objects.create(
            organization=self.org, name="PAs", slug="pas",
        )
        self.owner = User.objects.create_user(
            username="vmendi", password="pw", current_organization=self.org,
        )
        self.stranger = User.objects.create_user(
            username="alice", password="pw", current_organization=self.org,
        )
        self.aws_account = AWSAccount.objects.create(
            organization=self.org, name="Test Account",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Default", slug="default", aws_region="us-east-1",
        )
        self.pa_app = App.objects.create(
            organization=self.org, workspace=self.workspace,
            source_template=app_test_factories.make_source_template(),
            environment=self.env, name="VmendiPA", slug="vmendihermes",
            container_port=8000,
            health_check_path="/health", cpu=256, memory=512,
        )
        # Drop the auto-created open-access policy so only the self-ref policy is in play.
        Policy.objects.filter(
            organization=self.org, name=f"Default: {self.pa_app.name} open access",
        ).delete()
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=self.pa_app,
            key="app-type", value="personal-assistant",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=self.pa_app,
            key="owner", value="vmendi",
        )
        # Identity attribute carrying the username so it enters the effective-attr set.
        IdentityAttribute.objects.create(
            organization=self.org, user=self.owner, key="username", value="vmendi",
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.stranger, key="username", value="alice",
        )
        Policy.objects.create(
            organization=self.org, name="PA owner access",
            resource_type="app",
            identity_conditions=[{"key": "username", "value": "$resource.owner"}],
            resource_conditions=[{"key": "app-type", "value": "personal-assistant"}],
            actions=["app:use"],
        )

    def test_owner_is_granted_app_use(self) -> None:
        allowed = abac_service.evaluate_policies(
            organization=self.org, user=self.owner,
            resource=self.pa_app, resource_type="app",
        )
        self.assertIn("app:use", allowed)

    def test_non_owner_is_denied(self) -> None:
        allowed = abac_service.evaluate_policies(
            organization=self.org, user=self.stranger,
            resource=self.pa_app, resource_type="app",
        )
        self.assertNotIn("app:use", allowed)

    def test_missing_owner_tag_denies_owner(self) -> None:
        ResourceTag.objects.filter(
            organization=self.org, app=self.pa_app, key="owner",
        ).delete()
        allowed = abac_service.evaluate_policies(
            organization=self.org, user=self.owner,
            resource=self.pa_app, resource_type="app",
        )
        self.assertNotIn("app:use", allowed)

    def test_missing_username_attribute_denies(self) -> None:
        IdentityAttribute.objects.filter(
            organization=self.org, user=self.owner, key="username",
        ).delete()
        allowed = abac_service.evaluate_policies(
            organization=self.org, user=self.owner,
            resource=self.pa_app, resource_type="app",
        )
        self.assertNotIn("app:use", allowed)

    def test_identity_side_reference_evaluates(self) -> None:
        # Policy uses $identity.<key> on the resource side. Owner tag refers to
        # the identity's username attribute.
        Policy.objects.filter(organization=self.org, name="PA owner access").delete()
        Policy.objects.create(
            organization=self.org, name="PA owner (resource-side ref)",
            resource_type="app",
            identity_conditions=[{"key": "app-type", "value": "personal-assistant"}],
            # Intentional: identity conditions here are purely attribute literals;
            # we put the reference on the resource side instead.
            resource_conditions=[{"key": "owner", "value": "$identity.username"}],
            actions=["app:use"],
        )
        # The identity condition needs to match against attributes of the user.
        # Give both users the "app-type=personal-assistant" identity attribute so
        # only the resource-side ref discriminates.
        IdentityAttribute.objects.create(
            organization=self.org, user=self.owner,
            key="app-type", value="personal-assistant",
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.stranger,
            key="app-type", value="personal-assistant",
        )
        owner_allowed = abac_service.evaluate_policies(
            organization=self.org, user=self.owner,
            resource=self.pa_app, resource_type="app",
        )
        stranger_allowed = abac_service.evaluate_policies(
            organization=self.org, user=self.stranger,
            resource=self.pa_app, resource_type="app",
        )
        self.assertIn("app:use", owner_allowed)
        self.assertNotIn("app:use", stranger_allowed)

    def test_filter_permitted_resources_respects_self_ref(self) -> None:
        # Second PA owned by a different user.
        other_app = App.objects.create(
            organization=self.org, workspace=self.workspace,
            source_template=app_test_factories.make_source_template(),
            environment=self.env, name="AlicePA", slug="alice-hermes",
            container_port=8000,
            health_check_path="/health", cpu=256, memory=512,
        )
        Policy.objects.filter(
            organization=self.org, name=f"Default: {other_app.name} open access",
        ).delete()
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=other_app,
            key="app-type", value="personal-assistant",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=other_app,
            key="owner", value="alice",
        )
        visible_for_owner = abac_service.filter_permitted_resources(
            organization=self.org, user=self.owner,
            queryset=App.objects.filter(organization=self.org),
            resource_type="app", action="app:use",
        )
        self.assertEqual(list(visible_for_owner.values_list("slug", flat=True)), ["vmendihermes"])
        visible_for_stranger = abac_service.filter_permitted_resources(
            organization=self.org, user=self.stranger,
            queryset=App.objects.filter(organization=self.org),
            resource_type="app", action="app:use",
        )
        self.assertEqual(list(visible_for_stranger.values_list("slug", flat=True)), ["alice-hermes"])
