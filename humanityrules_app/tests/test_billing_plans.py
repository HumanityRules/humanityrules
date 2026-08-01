"""Tests for plan entitlements: the override schema, the gates that read them, and the trial grant.

The rules that are expensive to get wrong live here: a typo'd override is a save
error rather than a silently dead entitlement, capabilities reach the deploy path
from the plan booleans and nowhere else, the trial grant is written exactly once
through the balance lock, the agent cap counts live agents, and the broker's
snapshot stops spending at −5% of the grant rather than at zero.
"""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from humanityrules_app import models
from humanityrules_app.services.billing import entitlements, grants, plans
from humanityrules_app.services.infra_customer import deploy_app
from humanityrules_app.services.jobs import app_config_builder
from humanityrules_app.services import template_deploy_service


def _agent_template(slug: str) -> models.AppTemplate:
    """A policy-proxy-fronted, personal-assistant-tagged template: what max_agents counts."""
    return models.AppTemplate.objects.create(
        name=slug,
        slug=slug,
        description="",
        icon="",
        category="ai-assistant",
        cpu=256,
        memory=512,
        default_compute_mode="fargate",
        is_active=True,
        alb_target_container="policy-proxy",
        default_tags=[{"key": "app-type", "value": "personal-assistant"}],
        containers=[
            {
                "name": "agent",
                "image_source": "template",
                "template_path": "hermes_agent",
                "container_port": 8787,
                "health_check_path": "/health",
            },
            {
                "name": "policy-proxy",
                "image_source": "template",
                "template_path": "policy_proxy",
                "role": "policy_proxy",
                "upstream_container": "agent",
                "container_port": 8080,
            },
        ],
    )


def _plain_template(slug: str) -> models.AppTemplate:
    """A template that deploys something other than an agent, so the cap ignores it."""
    return models.AppTemplate.objects.create(
        name=slug,
        slug=slug,
        description="",
        icon="",
        category="web-app",
        cpu=256,
        memory=512,
        default_compute_mode="fargate",
        is_active=True,
        alb_target_container="app",
        containers=[
            {
                "name": "app",
                "image_source": "template",
                "template_path": "hermes_agent",
                "container_port": 8000,
                "health_check_path": "/health",
            },
        ],
    )


class TestPlanRegistry(SimpleTestCase):
    """The numbers the product sells, and the shape the override schema validates against."""

    def test_trial_is_free_with_a_one_time_500_credit_grant(self) -> None:
        trial = plans.PLANS[plans.TRIAL]

        self.assertIsNone(trial.price_usd_month)
        self.assertEqual(trial.monthly_credit_grant, 500)
        self.assertFalse(trial.always_on)
        self.assertEqual(trial.trial_runtime_days, 7)
        self.assertEqual(trial.max_agents, 1)

    def test_operator_is_39_dollars_for_2000_credits_always_on(self) -> None:
        operator = plans.PLANS[plans.OPERATOR]

        self.assertEqual(operator.price_usd_month, Decimal("39"))
        self.assertEqual(operator.monthly_credit_grant, 2000)
        self.assertTrue(operator.always_on)
        self.assertIsNone(operator.trial_runtime_days)
        self.assertEqual(operator.max_agents, 1)

    def test_customer_cloud_starts_at_team(self) -> None:
        self.assertFalse(plans.PLANS[plans.TRIAL].customer_cloud)
        self.assertFalse(plans.PLANS[plans.OPERATOR].customer_cloud)
        self.assertTrue(plans.PLANS[plans.TEAM].customer_cloud)
        self.assertTrue(plans.PLANS[plans.ENTERPRISE].customer_cloud)

    def test_on_demand_is_off_everywhere(self) -> None:
        for name, plan in plans.PLANS.items():
            with self.subTest(plan=name):
                self.assertFalse(plan.on_demand_allowed)

    def test_every_organization_plan_choice_has_a_config(self) -> None:
        self.assertEqual(set(models.Organization.Plan.values), set(plans.PLANS))

    def test_capability_slugs_match_the_deploy_paths_constant(self) -> None:
        self.assertEqual(
            plans.CAPABILITY_SLUG_BY_FIELD["bedrock_enabled"],
            deploy_app.PLATFORM_CAPABILITY_BEDROCK_RUNTIME,
        )


class TestOverrideValidation(SimpleTestCase):
    """A typo'd key is a save error, not a silently dead exception."""

    def test_known_keys_with_the_right_types_pass(self) -> None:
        plans.validate_overrides(overrides={
            "monthly_credit_grant": 5000,
            "always_on": True,
            "trial_runtime_days": None,
            "max_agents": 4,
            "customer_cloud": True,
            "bedrock_enabled": True,
            "on_demand_allowed": False,
        })

    def test_empty_overrides_pass(self) -> None:
        plans.validate_overrides(overrides={})

    def test_typo_key_is_refused(self) -> None:
        with self.assertRaisesMessage(plans.PlanOverrideError, "'bedrock_enabeld' is not a plan entitlement"):
            plans.validate_overrides(overrides={"bedrock_enabeld": True})

    def test_price_is_refused_as_an_override(self) -> None:
        with self.assertRaisesMessage(plans.PlanOverrideError, "not overridable"):
            plans.validate_overrides(overrides={"price_usd_month": 0})

    def test_wrong_type_is_refused(self) -> None:
        with self.assertRaisesMessage(plans.PlanOverrideError, "must be a boolean"):
            plans.validate_overrides(overrides={"bedrock_enabled": "yes"})

    def test_a_boolean_is_not_a_credit_count(self) -> None:
        with self.assertRaisesMessage(plans.PlanOverrideError, "must be int"):
            plans.validate_overrides(overrides={"monthly_credit_grant": True})

    def test_nullable_field_accepts_null_and_int_only(self) -> None:
        plans.validate_overrides(overrides={"trial_runtime_days": None})
        plans.validate_overrides(overrides={"trial_runtime_days": 30})

        with self.assertRaises(plans.PlanOverrideError):
            plans.validate_overrides(overrides={"trial_runtime_days": "30"})

    def test_non_object_overrides_are_refused(self) -> None:
        with self.assertRaisesMessage(plans.PlanOverrideError, "must be a JSON object"):
            plans.validate_overrides(overrides=["bedrock_enabled"])


class TestOverrideValidationOnSave(TestCase):

    def test_saving_a_typo_raises(self) -> None:
        with self.assertRaises(plans.PlanOverrideError):
            models.Organization.objects.create(
                name="Typo Org", slug="typo-org", plan_overrides={"max_agent": 5},
            )

        self.assertFalse(models.Organization.objects.filter(slug="typo-org").exists())

    def test_saving_a_valid_override_persists_it(self) -> None:
        organization = models.Organization.objects.create(
            name="Comped Org", slug="comped-org", plan_overrides={"max_agents": 5},
        )

        organization.refresh_from_db()
        self.assertEqual(organization.plan_overrides, {"max_agents": 5})

    def test_a_new_organization_starts_on_trial_with_no_overrides(self) -> None:
        organization = models.Organization.objects.create(name="Fresh Org", slug="fresh-org")

        self.assertEqual(organization.plan, plans.TRIAL)
        self.assertEqual(organization.plan_overrides, {})


class TestEffectivePlan(TestCase):
    """The only gate API: plan config with overrides folded in, whole fields at a time."""

    def test_no_overrides_returns_the_plans_config(self) -> None:
        organization = models.Organization.objects.create(name="Plain", slug="plain", plan=plans.OPERATOR)

        self.assertEqual(plans.effective_plan(organization=organization), plans.PLANS[plans.OPERATOR])

    def test_an_override_replaces_only_its_own_field(self) -> None:
        organization = models.Organization.objects.create(
            name="Comped", slug="comped-plan", plan=plans.TRIAL,
            plan_overrides={"monthly_credit_grant": 5000, "max_agents": 3},
        )

        plan = plans.effective_plan(organization=organization)

        self.assertEqual(plan.monthly_credit_grant, 5000)
        self.assertEqual(plan.max_agents, 3)
        self.assertEqual(plan.trial_runtime_days, plans.PLANS[plans.TRIAL].trial_runtime_days)
        self.assertFalse(plan.always_on)

    def test_an_unknown_plan_fails_loudly(self) -> None:
        organization = models.Organization.objects.create(name="Bogus", slug="bogus-plan")
        models.Organization.objects.filter(id=organization.id).update(plan="platinum")
        organization.refresh_from_db()

        with self.assertRaisesMessage(plans.PlanOverrideError, "unknown plan 'platinum'"):
            plans.effective_plan(organization=organization)

    def test_an_override_written_behind_validation_still_fails_loudly(self) -> None:
        organization = models.Organization.objects.create(name="Hand Edited", slug="hand-edited")
        models.Organization.objects.filter(id=organization.id).update(plan_overrides={"max_agent": 9})
        organization.refresh_from_db()

        with self.assertRaises(plans.PlanOverrideError):
            plans.effective_plan(organization=organization)


class TestCapabilitySlugDerivation(TestCase):
    """The deploy path reads slugs; the plan booleans are where they come from."""

    def test_bedrock_boolean_becomes_the_infra_slug(self) -> None:
        plan = plans.PLANS[plans.TRIAL]

        self.assertEqual(plan.platform_capability_slugs(), [])
        self.assertEqual(
            plans.PLANS[plans.ENTERPRISE].platform_capability_slugs(), ["bedrock-runtime"],
        )

    def test_the_config_builder_reads_the_plan(self) -> None:
        organization = models.Organization.objects.create(
            name="Cap Plan Org", slug="cap-plan-org", plan_overrides={"bedrock_enabled": True},
        )

        capabilities = app_config_builder.effective_platform_capabilities(organization=organization)

        self.assertEqual(capabilities, ["bedrock-runtime"])

    def test_an_enterprise_org_gets_bedrock_without_an_override(self) -> None:
        organization = models.Organization.objects.create(
            name="Ent Org", slug="ent-org", plan=plans.ENTERPRISE,
        )

        capabilities = app_config_builder.effective_platform_capabilities(organization=organization)

        self.assertEqual(capabilities, ["bedrock-runtime"])

    def test_an_override_can_take_bedrock_away_from_a_plan_that_has_it(self) -> None:
        organization = models.Organization.objects.create(
            name="Ent No Bedrock", slug="ent-no-bedrock", plan=plans.ENTERPRISE,
            plan_overrides={"bedrock_enabled": False},
        )

        capabilities = app_config_builder.effective_platform_capabilities(organization=organization)

        self.assertEqual(capabilities, [])


class TestTrialGrant(TestCase):
    """One grant per organization, written through the balance lock, keyed so it cannot repeat."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Grant Org", slug="grant-org")

    def ledger(self) -> list[models.BillingLedgerEntry]:
        return list(models.BillingLedgerEntry.objects.filter(organization=self.organization))

    def balance_credits(self) -> Decimal:
        balance = models.BillingBalance.objects.filter(organization=self.organization).first()
        return balance.credits if balance is not None else Decimal(0)

    def test_the_grant_posts_500_credits_and_moves_the_balance(self) -> None:
        written = grants.grant_trial_credits(organization=self.organization)

        entries = self.ledger()
        self.assertTrue(written)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].type, models.BillingLedgerEntry.Type.GRANT)
        self.assertEqual(entries[0].amount, Decimal(500))
        self.assertEqual(entries[0].idempotency_key, f"grant:trial:{self.organization.id}")
        self.assertEqual(self.balance_credits(), Decimal(500))

    def test_the_entry_stamps_the_plan_and_its_version(self) -> None:
        grants.grant_trial_credits(organization=self.organization)

        self.assertEqual(self.ledger()[0].metadata, {"plan": "trial", "plan_version": plans.PLAN_VERSION})

    def test_a_second_call_is_a_no_op(self) -> None:
        grants.grant_trial_credits(organization=self.organization)
        written_again = grants.grant_trial_credits(organization=self.organization)

        self.assertFalse(written_again)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.balance_credits(), Decimal(500))

    def test_an_override_raises_the_granted_amount(self) -> None:
        self.organization.plan_overrides = {"monthly_credit_grant": 1500}
        self.organization.save(update_fields=["plan_overrides"])

        grants.grant_trial_credits(organization=self.organization)

        self.assertEqual(self.balance_credits(), Decimal(1500))

    def test_the_grant_does_not_disturb_an_existing_balance(self) -> None:
        models.BillingLedgerEntry.objects.create(
            organization=self.organization,
            type=models.BillingLedgerEntry.Type.CHARGE,
            amount=Decimal(-20),
            idempotency_key="charge:existing",
            description="Agent usage",
            metadata={},
        )
        models.BillingBalance.objects.create(organization=self.organization, credits=Decimal(-20))

        grants.grant_trial_credits(organization=self.organization)

        self.assertEqual(self.balance_credits(), Decimal(480))

    def test_a_grant_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            grants._write_grant(
                organization_id=self.organization.id,
                credits=Decimal(0),
                idempotency_key="grant:bogus",
                description="",
                metadata={},
            )

    def test_the_balance_equals_the_ledger_after_granting(self) -> None:
        grants.grant_trial_credits(organization=self.organization)

        call_command("humr_billing_verify", org_slug=self.organization.slug, stdout=StringIO())


class TestAgentLimit(TestCase):
    """max_agents is enforced where agents are created, as a validation error."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Cap Org", slug="agent-cap-org")
        self.user = models.User.objects.create_user(
            username="cap-user", password="x", current_organization=self.organization,
        )
        self.workspace = models.Workspace.objects.create(
            organization=self.organization, name="Agents", slug="agents",
        )
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization, name="Cap AWS",
        )
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account, name="Sandbox", slug="sandbox",
            aws_region="us-east-1", status=models.Environment.Status.READY,
        )
        self.template = _agent_template(slug="agent-template")

    def deploy(self, app_slug: str) -> models.App:
        return template_deploy_service.deploy_from_template(
            template=self.template,
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            app_name=app_slug,
            app_slug=app_slug,
            created_by=self.user,
            runtime_variable_overrides=None,
            owner_username=self.user.username,
            compute_mode="fargate",
            label="",
        )

    def test_the_first_agent_deploys_on_trial(self) -> None:
        app = self.deploy(app_slug="agentone")

        self.assertEqual(app.organization, self.organization)

    def test_the_second_agent_is_refused(self) -> None:
        self.deploy(app_slug="agentone")

        with self.assertRaisesMessage(ValueError, "Your plan includes 1 agent(s) and you already have 1"):
            self.deploy(app_slug="agenttwo")

        self.assertEqual(models.App.objects.filter(organization=self.organization).count(), 1)

    def test_an_override_raises_the_cap(self) -> None:
        self.organization.plan_overrides = {"max_agents": 2}
        self.organization.save(update_fields=["plan_overrides"])

        self.deploy(app_slug="agentone")
        second = self.deploy(app_slug="agenttwo")

        self.assertEqual(second.slug, "agenttwo")

    def test_an_agent_being_removed_no_longer_counts(self) -> None:
        first = self.deploy(app_slug="agentone")
        models.App.objects.filter(id=first.id).update(job_status=models.App.JobStatus.REMOVAL_PENDING)

        second = self.deploy(app_slug="agenttwo")

        self.assertEqual(second.slug, "agenttwo")

    def test_another_organizations_agents_do_not_count(self) -> None:
        other_organization = models.Organization.objects.create(name="Other", slug="other-cap-org")
        other_workspace = models.Workspace.objects.create(
            organization=other_organization, name="Agents", slug="agents",
        )
        other_account = models.AWSAccount.objects.create(
            organization=other_organization, name="Other AWS",
        )
        other_environment = models.Environment.objects.create(
            aws_account=other_account, name="Sandbox", slug="othersandbox",
            aws_region="us-east-1", status=models.Environment.Status.READY,
        )
        template_deploy_service.deploy_from_template(
            template=self.template,
            organization=other_organization,
            workspace=other_workspace,
            environment=other_environment,
            app_name="theirs",
            app_slug="theirs",
            created_by=self.user,
            runtime_variable_overrides=None,
            owner_username=self.user.username,
            compute_mode="fargate",
            label="",
        )

        app = self.deploy(app_slug="agentone")

        self.assertEqual(app.organization, self.organization)

    def test_a_non_agent_app_is_not_capped(self) -> None:
        self.deploy(app_slug="agentone")
        plain = _plain_template(slug="plain-template")

        app = template_deploy_service.deploy_from_template(
            template=plain,
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            app_name="webthing",
            app_slug="webthing",
            created_by=self.user,
            runtime_variable_overrides=None,
            owner_username=None,
            compute_mode="fargate",
            label="",
        )

        self.assertEqual(app.slug, "webthing")


class TestEntitlementSnapshot(TestCase):
    """What the broker enforces: the balance, the grant it is measured against, and the −5% floor."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Snap Org", slug="snap-org")

    def set_balance(self, credits: Decimal) -> None:
        models.BillingBalance.objects.update_or_create(
            organization=self.organization, defaults={"credits": credits},
        )

    def test_a_fresh_trial_reports_its_grant(self) -> None:
        grants.grant_trial_credits(organization=self.organization)

        snapshot = entitlements.entitlement_snapshot(organization=self.organization)

        self.assertEqual(snapshot.credits_remaining, 500)
        self.assertEqual(snapshot.monthly_grant, 500)
        self.assertIsNone(snapshot.renewal_date)
        self.assertEqual(snapshot.plan, "trial")
        self.assertFalse(snapshot.exhausted)

    def test_an_organization_with_no_balance_row_reads_as_zero(self) -> None:
        snapshot = entitlements.entitlement_snapshot(organization=self.organization)

        self.assertEqual(snapshot.credits_remaining, 0)
        self.assertFalse(snapshot.exhausted)

    def test_zero_credits_is_not_yet_exhausted(self) -> None:
        self.set_balance(credits=Decimal(0))

        self.assertFalse(entitlements.entitlement_snapshot(organization=self.organization).exhausted)

    def test_just_above_the_floor_still_spends(self) -> None:
        self.set_balance(credits=Decimal(-24))

        self.assertFalse(entitlements.entitlement_snapshot(organization=self.organization).exhausted)

    def test_the_floor_itself_is_exhausted(self) -> None:
        self.set_balance(credits=Decimal(-25))

        snapshot = entitlements.entitlement_snapshot(organization=self.organization)

        self.assertEqual(snapshot.credits_remaining, -25)
        self.assertTrue(snapshot.exhausted)

    def test_the_floor_follows_the_plans_grant(self) -> None:
        self.organization.plan = plans.OPERATOR
        self.organization.save(update_fields=["plan"])
        self.set_balance(credits=Decimal(-99))

        snapshot = entitlements.entitlement_snapshot(organization=self.organization)

        self.assertEqual(snapshot.monthly_grant, 2000)
        self.assertFalse(snapshot.exhausted)

        self.set_balance(credits=Decimal(-100))

        self.assertTrue(entitlements.entitlement_snapshot(organization=self.organization).exhausted)

    def test_the_floor_follows_an_override(self) -> None:
        self.organization.plan_overrides = {"monthly_credit_grant": 5000}
        self.organization.save(update_fields=["plan_overrides"])
        self.set_balance(credits=Decimal(-249))

        snapshot = entitlements.entitlement_snapshot(organization=self.organization)

        self.assertEqual(snapshot.monthly_grant, 5000)
        self.assertFalse(snapshot.exhausted)

    def test_the_payload_carries_exactly_the_broker_contract(self) -> None:
        grants.grant_trial_credits(organization=self.organization)

        payload = entitlements.snapshot_payload(organization=self.organization)

        self.assertEqual(payload, {
            "credits_remaining": 500,
            "monthly_grant": 500,
            "renewal_date": None,
            "plan": "trial",
            "exhausted": False,
        })


class TestTrialGrantAtSignup(TestCase):
    """A new organization has its credits before it has an agent."""

    def _seed_pending_workos_session(self, email: str) -> None:
        session = self.client.session
        session["pending_workos_user"] = {
            "workos_user_id": "user_signup123",
            "email": email,
            "first_name": "New",
            "last_name": "Friend",
        }
        session.save()

    def test_signup_writes_the_trial_grant(self) -> None:
        self._seed_pending_workos_session(email="friend@example.com")

        response = self.client.post("/onboarding/", {"organization_name": "Friend Co"})

        self.assertEqual(response.status_code, 302)
        organization = models.Organization.objects.get(slug="friend-co")
        self.assertEqual(organization.plan, plans.TRIAL)
        entry = models.BillingLedgerEntry.objects.get(organization=organization)
        self.assertEqual(entry.idempotency_key, f"grant:trial:{organization.id}")
        self.assertEqual(entry.amount, Decimal(500))
        self.assertEqual(models.BillingBalance.objects.get(organization=organization).credits, Decimal(500))

    def test_repeating_the_grant_cannot_double_grant_a_signed_up_org(self) -> None:
        self._seed_pending_workos_session(email="friend@example.com")
        self.client.post("/onboarding/", {"organization_name": "Friend Co"})

        organization = models.Organization.objects.get(slug="friend-co")
        self.assertFalse(grants.grant_trial_credits(organization=organization))
        self.assertEqual(models.BillingLedgerEntry.objects.filter(organization=organization).count(), 1)
        self.assertEqual(models.BillingBalance.objects.get(organization=organization).credits, Decimal(500))
