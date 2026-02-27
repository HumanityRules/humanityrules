"""Tests for the ABAC policy evaluation engine (devopshero_app/services/abac.py)."""

from django.db.models import QuerySet
from django.test import TestCase

from devopshero_app.models import (
    AWSAccount,
    App,
    Environment,
    Group,
    GroupAttribute,
    GroupMembership,
    IdentityAttribute,
    Organization,
    Policy,
    Repository,
    ResourceTag,
    User,
    Workspace,
)
from django.core.exceptions import ValidationError

from devopshero_app.services import abac
from devopshero_app.services.abac import _conditions_match


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
        attrs = abac.get_effective_attributes(organization=self.org, user=self.user)
        self.assertEqual(attrs, [("authenticated", "true", "system")])

    def test_direct_attributes_have_direct_source(self) -> None:
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="role", value="developer",
        )
        attrs = abac.get_effective_attributes(organization=self.org, user=self.user)
        self.assertIn(("authenticated", "true", "system"), attrs)
        self.assertIn(("role", "developer", "direct"), attrs)

    def test_group_attributes_have_group_source(self) -> None:
        group = Group.objects.create(organization=self.org, name="Engineers")
        GroupMembership.objects.create(group=group, user=self.user)
        GroupAttribute.objects.create(group=group, key="team", value="backend")

        attrs = abac.get_effective_attributes(organization=self.org, user=self.user)
        self.assertIn(("team", "backend", "group:Engineers"), attrs)

    def test_multiple_groups_returns_union(self) -> None:
        g1 = Group.objects.create(organization=self.org, name="Frontend")
        g2 = Group.objects.create(organization=self.org, name="Backend")
        GroupMembership.objects.create(group=g1, user=self.user)
        GroupMembership.objects.create(group=g2, user=self.user)
        GroupAttribute.objects.create(group=g1, key="team", value="frontend")
        GroupAttribute.objects.create(group=g2, key="team", value="backend")

        attrs = abac.get_effective_attributes(organization=self.org, user=self.user)
        self.assertIn(("team", "frontend", "group:Frontend"), attrs)
        self.assertIn(("team", "backend", "group:Backend"), attrs)

    def test_same_key_from_direct_and_group_not_deduplicated(self) -> None:
        IdentityAttribute.objects.create(
            organization=self.org, user=self.user, key="team", value="platform",
        )
        group = Group.objects.create(organization=self.org, name="DataTeam")
        GroupMembership.objects.create(group=group, user=self.user)
        GroupAttribute.objects.create(group=group, key="team", value="data")

        attrs = abac.get_effective_attributes(organization=self.org, user=self.user)
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
        self.repo = Repository.objects.create(
            organization=self.org, provider="github", name="repo",
            full_name="org/repo", clone_url="https://github.com/org/repo.git",
        )

    def _make_app(self, name: str, slug: str) -> App:
        return App.objects.create(
            organization=self.org, workspace=self.workspace, repository=self.repo,
            name=name, slug=slug, app_type="web", build_strategy="dockerfile",
            branch="main", container_port=8000, cpu=256, memory=512,
            health_check_path="/health",
        )

    def test_app_direct_tags_source_is_direct(self) -> None:
        app = self._make_app(name="myapp", slug="myapp")
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=app,
            key="tier", value="production",
        )
        tags = abac.get_effective_tags(organization=self.org, resource=app, resource_type="app")
        direct_tags = [(k, v, s) for k, v, s in tags if s == "direct"]
        self.assertIn(("tier", "production", "direct"), direct_tags)

    def test_app_inherits_workspace_tags(self) -> None:
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="finance",
        )
        app = self._make_app(name="finapp", slug="finapp")

        tags = abac.get_effective_tags(organization=self.org, resource=app, resource_type="app")
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

        tags = abac.get_effective_tags(organization=self.org, resource=app, resource_type="app")
        self.assertIn(("tier", "production", "direct"), tags)
        self.assertIn(("domain", "finance", f"inherited:{self.workspace.name}"), tags)

    def test_workspace_tags_are_always_direct(self) -> None:
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="platform",
        )
        tags = abac.get_effective_tags(organization=self.org, resource=self.workspace, resource_type="workspace")
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

        tags = abac.get_effective_tags(organization=self.org, resource=env, resource_type="environment")
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

        tags = abac.get_effective_tags(organization=self.org, resource=self.workspace, resource_type="workspace")
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

        tags = abac.get_effective_tags(organization=self.org, resource=app, resource_type="app")
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
        abac.validate_policy_conditions(
            identity_conditions=[{"key": "*", "value": "*"}],
            resource_conditions=[{"key": "domain", "value": "finance"}],
        )

    def test_sole_wildcard_resource_is_valid(self) -> None:
        abac.validate_policy_conditions(
            identity_conditions=[{"key": "role", "value": "developer"}],
            resource_conditions=[{"key": "*", "value": "*"}],
        )

    def test_both_wildcard_is_valid(self) -> None:
        abac.validate_policy_conditions(
            identity_conditions=[{"key": "*", "value": "*"}],
            resource_conditions=[{"key": "*", "value": "*"}],
        )

    def test_empty_conditions_are_valid(self) -> None:
        abac.validate_policy_conditions(identity_conditions=[], resource_conditions=[])

    def test_mixed_wildcard_identity_raises(self) -> None:
        with self.assertRaises(ValidationError) as cm:
            abac.validate_policy_conditions(
                identity_conditions=[{"key": "*", "value": "*"}, {"key": "team", "value": "finance"}],
                resource_conditions=[{"key": "domain", "value": "finance"}],
            )
        self.assertIn("Identity", str(cm.exception))

    def test_mixed_wildcard_resource_raises(self) -> None:
        with self.assertRaises(ValidationError) as cm:
            abac.validate_policy_conditions(
                identity_conditions=[{"key": "role", "value": "developer"}],
                resource_conditions=[{"key": "*", "value": "*"}, {"key": "tier", "value": "production"}],
            )
        self.assertIn("Resource", str(cm.exception))

    def test_multiple_non_wildcard_conditions_valid(self) -> None:
        abac.validate_policy_conditions(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
            organization=self.org, user=self.user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertIn("workspace:admin", result)
        self.assertIn("workspace:edit", result)
        self.assertNotIn("workspace:view", result)

    def test_no_policies_returns_empty(self) -> None:
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
            organization=self.org, user=bare_user,
            resource=self.workspace, resource_type="workspace",
        )
        self.assertNotIn("workspace:view", result)


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
        result = abac.evaluate_policies_unscoped(
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
        result = abac.evaluate_policies_unscoped(
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
        result = abac.evaluate_policies_unscoped(
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
        result = abac.evaluate_policies_unscoped(
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
        result = abac.filter_permitted_resources(
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
        result = abac.filter_permitted_resources(
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
        result = abac.filter_permitted_resources(
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

        view_result = abac.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        self.assertEqual(
            set(view_result.values_list("pk", flat=True)),
            set(qs.values_list("pk", flat=True)),
        )

        edit_result = abac.filter_permitted_resources(
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
        result = abac.filter_permitted_resources(
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
        result = abac.filter_permitted_resources(
            organization=self.org, user=self.user,
            queryset=qs, resource_type="workspace", action="workspace:view",
        )
        result_pks = set(result.values_list("pk", flat=True))
        self.assertIn(self.ws_eng.pk, result_pks)
        self.assertIn(self.ws_hr.pk, result_pks)
        self.assertNotIn(self.ws_fin.pk, result_pks)


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
            abac.filter_permitted_resources(
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
        result = abac.filter_permitted_resources(
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
        result = abac.evaluate_policies(
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
        result = abac.evaluate_policies(
            organization=self.org_a, user=self.user_a,
            resource=self.ws_a, resource_type="workspace",
        )
        self.assertNotIn("workspace:admin", result)

    def test_evaluate_policies_raises_on_resource_org_mismatch(self) -> None:
        """Passing a resource from a different org is a caller bug — must raise."""
        with self.assertRaises(ValueError) as cm:
            abac.evaluate_policies(
                organization=self.org_a, user=self.user_a,
                resource=self.ws_b, resource_type="workspace",
            )
        self.assertIn("belongs to organization", str(cm.exception))

    def test_check_action_raises_on_resource_org_mismatch(self) -> None:
        """check_action inherits the resource-org assertion from evaluate_policies."""
        with self.assertRaises(ValueError):
            abac.check_action(
                organization=self.org_a, user=self.user_a,
                resource=self.ws_b, resource_type="workspace", action="workspace:view",
            )

    def test_get_effective_tags_raises_on_resource_org_mismatch(self) -> None:
        """get_effective_tags rejects a resource from a foreign org."""
        with self.assertRaises(ValueError):
            abac.get_effective_tags(
                organization=self.org_a, resource=self.ws_b, resource_type="workspace",
            )


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
        self.assertTrue(abac.is_org_admin(organization=self.org, user=self.user))

    def test_group_inherited_admin(self) -> None:
        group = Group.objects.create(organization=self.org, name="Admins")
        GroupMembership.objects.create(group=group, user=self.user)
        GroupAttribute.objects.create(group=group, key="org-role", value="admin")
        self.assertTrue(abac.is_org_admin(organization=self.org, user=self.user))

    def test_no_admin_attribute(self) -> None:
        self.assertFalse(abac.is_org_admin(organization=self.org, user=self.user))
