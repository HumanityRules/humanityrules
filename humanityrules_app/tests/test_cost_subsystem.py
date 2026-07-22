"""Tests for the per-app cost subsystem (humanityrules_app/services/cost/).

Covers the deterministic core without touching AWS: Bedrock pricing math, the source-agnostic
cost_refresh (window/freeze/upsert/rolling-24h) driven by a stub source, the cache-first panel
context + chart geometry, and the HTMX panel view.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.test import Client, TestCase

from humanityrules_app.models import (
    AWSAccount,
    App,
    AppDailyCost,
    CostRefreshJob,
    Environment,
    Organization,
    OrganizationMembership,
    Repository,
    User,
    Workspace,
)
from humanityrules_app.services.cost import bedrock_pricing as pricing
from humanityrules_app.services.cost import cost_refresh, panel
from humanityrules_app.services.cost.cost_source import DailyCostRow
from humanityrules_app.services.jobs import job_worker

_REAL_RECORD_ARN = "arn:aws:bedrock:us-east-1:266117665083:inference-profile/us.anthropic.claude-sonnet-4-6"


class TestBedrockPricing(TestCase):
    """The pricing table + normalization + cost_for, checked against the design doc's real record."""

    def test_normalize_strips_geo_and_global_prefixes(self) -> None:
        self.assertEqual(pricing.normalize_model_id(model_id=_REAL_RECORD_ARN), ("anthropic.claude-sonnet-4-6", "geo"))
        self.assertEqual(
            pricing.normalize_model_id(model_id="global.anthropic.claude-opus-4-8"),
            ("anthropic.claude-opus-4-8", "global"),
        )
        self.assertEqual(
            pricing.normalize_model_id(model_id="anthropic.claude-haiku-4-5"), ("anthropic.claude-haiku-4-5", "geo"),
        )

    def test_real_cache_priming_record_is_priced_exactly(self) -> None:
        # The doc's real record: 3 fresh input tokens but 18,779 written to cache — the cache write
        # dominates, so input-only accounting would drastically undercount.
        breakdown = pricing.cost_for(
            model_id=_REAL_RECORD_ARN, source_region="us-east-1",
            input_tokens=3, output_tokens=12, cache_write_tokens=18779, cache_read_tokens=0,
        )
        expected = (
            Decimal(3) / 10**6 * Decimal("3.30")
            + Decimal(12) / 10**6 * Decimal("16.50")
            + Decimal(18779) / 10**6 * Decimal("4.125")
        )
        self.assertEqual(breakdown.cost_usd, expected)
        self.assertTrue(breakdown.priced)
        self.assertFalse(breakdown.estimate)

    def test_global_tier_uses_global_rates(self) -> None:
        breakdown = pricing.cost_for(
            model_id="global.anthropic.claude-opus-4-8", source_region="us-east-1",
            input_tokens=1_000_000, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0,
        )
        self.assertEqual(breakdown.cost_usd, Decimal("5.00"))
        self.assertFalse(breakdown.estimate)

    def test_unknown_model_is_unpriced_not_mispriced(self) -> None:
        breakdown = pricing.cost_for(
            model_id="us.amazon.nova-pro", source_region="us-east-1",
            input_tokens=1000, output_tokens=1000, cache_write_tokens=0, cache_read_tokens=0,
        )
        self.assertEqual(breakdown.cost_usd, Decimal(0))
        self.assertFalse(breakdown.priced)

    def test_non_us_region_flags_estimate(self) -> None:
        breakdown = pricing.cost_for(
            model_id="eu.anthropic.claude-sonnet-4-6", source_region="eu-west-1",
            input_tokens=1000, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0,
        )
        self.assertTrue(breakdown.priced)
        self.assertTrue(breakdown.estimate)


class TestChartYAxisTicks(TestCase):
    """The y-axis 'nice' tick helper: starts at 0, ascends, and the top tick always covers the max."""

    def test_top_tick_always_covers_max(self) -> None:
        for max_value in [0.096298, 5.0, 0.4, 1.7, 0.008, 0.0001, 12.3, 99.0]:
            ticks = panel._nice_axis_ticks(max_value=max_value, target_count=4)
            self.assertEqual(ticks[0], 0.0)
            self.assertGreaterEqual(ticks[-1], max_value)  # a bar must never exceed the top tick
            self.assertEqual(ticks, sorted(ticks))
            self.assertEqual(len(ticks), len(set(ticks)))  # no duplicate ticks

    def test_zero_max_is_safe(self) -> None:
        self.assertEqual(panel._nice_axis_ticks(max_value=0.0, target_count=4), [0.0])


class _StubSource:
    """A CostSource that returns canned rows and records the windows it was asked to collect."""

    key = "stub"

    def __init__(self, rows: list[DailyCostRow], rolling_24h: Decimal) -> None:
        self._rows = rows
        self._rolling_24h = rolling_24h
        self.collect_calls: list[tuple] = []

    def collect(self, app: App, start_date, end_date) -> list[DailyCostRow]:
        self.collect_calls.append((start_date, end_date))
        return self._rows

    def rolling_24h_usd(self, app: App) -> Decimal:
        return self._rolling_24h


class _CostFixtureMixin:
    """Shared org/app/environment fixtures for the DB-backed cost tests."""

    def _build_fixtures(self) -> None:
        self.org = Organization.objects.create(name="Cost Org", slug="cost-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Prod", aws_account_id="111122223333")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Default", slug="default", aws_region="us-east-1",
        )
        self.repository = Repository.objects.create(
            organization=self.org, provider="github", name="hermes", full_name="org/hermes",
            default_branch="main", clone_url="https://github.com/org/hermes.git",
        )
        self.workspace = Workspace.objects.create(organization=self.org, name="Eng", slug="eng")
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, environment=self.env, repository=self.repository,
            name="Hermes", slug="hermes", build_strategy=App.BuildStrategy.DOCKERFILE,
            container_port=8000, health_check_path="/health", cpu=256, memory=512,
        )

    def _row(self, day, cost: str, estimate: bool) -> DailyCostRow:
        return DailyCostRow(
            environment_id=self.env.id, date=day, subkey="anthropic.claude-sonnet-4-6",
            cost_usd=Decimal(cost), details={"priced": True, "estimate": estimate, "input_tokens": 10},
        )


class TestCostOrchestrator(_CostFixtureMixin, TestCase):
    """Window selection, idempotent upsert, freeze, and the distinct rolling-24h sum."""

    def setUp(self) -> None:
        self._build_fixtures()
        self.today = datetime.now(tz=timezone.utc).date()

    def test_first_run_backfills_upserts_and_freezes_aged_days(self) -> None:
        rows = [
            self._row(day=self.today, cost="1.00", estimate=False),
            self._row(day=self.today - timedelta(days=1), cost="2.00", estimate=False),
            self._row(day=self.today - timedelta(days=5), cost="3.00", estimate=False),
        ]
        stub = _StubSource(rows=rows, rolling_24h=Decimal("0.5"))
        with patch.object(cost_refresh, "COST_SOURCES", [stub]):
            summary = cost_refresh.refresh_app(app=self.app)

        # First run backfills the full retention window (30 days back).
        self.assertEqual(stub.collect_calls[0][0], self.today - timedelta(days=29))
        self.assertEqual(stub.collect_calls[0][1], self.today)
        self.assertEqual(AppDailyCost.objects.filter(app=self.app).count(), 3)

        aged = AppDailyCost.objects.get(app=self.app, date=self.today - timedelta(days=5))
        today_row = AppDailyCost.objects.get(app=self.app, date=self.today)
        self.assertTrue(aged.is_final)  # < today-1 → frozen
        self.assertFalse(today_row.is_final)  # today stays live
        self.assertEqual(summary["rolling_24h_usd"], "0.5")  # distinct from the day bins

    def test_second_run_only_recomputes_from_after_last_frozen_day(self) -> None:
        # A past row (today-3) is produced and frozen on run 1; run 2 must start the day after it
        # rather than re-backfilling the whole retention window.
        rows = [
            self._row(day=self.today, cost="1.00", estimate=False),
            self._row(day=self.today - timedelta(days=3), cost="1.00", estimate=False),
        ]
        stub = _StubSource(rows=rows, rolling_24h=Decimal(0))
        with patch.object(cost_refresh, "COST_SOURCES", [stub]):
            cost_refresh.refresh_app(app=self.app)
            stub.collect_calls.clear()
            cost_refresh.refresh_app(app=self.app)

        self.assertEqual(stub.collect_calls[0][0], self.today - timedelta(days=2))

    def test_upsert_is_idempotent(self) -> None:
        stub = _StubSource(rows=[self._row(day=self.today, cost="1.00", estimate=False)], rolling_24h=Decimal(0))
        with patch.object(cost_refresh, "COST_SOURCES", [stub]):
            cost_refresh.refresh_app(app=self.app)
            stub._rows = [self._row(day=self.today, cost="4.00", estimate=False)]
            cost_refresh.refresh_app(app=self.app)

        rows = AppDailyCost.objects.filter(app=self.app, date=self.today)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().cost_usd, Decimal("4.00"))  # updated in place


class TestCostPanel(_CostFixtureMixin, TestCase):
    """Cache-first panel context, dedup, chart geometry, and the rolling-24h headline."""

    def setUp(self) -> None:
        self._build_fixtures()
        self.today = datetime.now(tz=timezone.utc).date()

    def _seed(self, day, subkey: str, cost: str, estimate: bool) -> None:
        AppDailyCost.objects.create(
            organization=self.org, app=self.app, environment=self.env, source="bedrock", date=day,
            subkey=subkey, cost_usd=Decimal(cost), details={"priced": True, "estimate": estimate},
        )

    def test_context_has_chart_geometry_legend_and_estimate_flag(self) -> None:
        self._seed(day=self.today, subkey="anthropic.claude-sonnet-4-6", cost="0.20", estimate=False)
        self._seed(day=self.today, subkey="anthropic.claude-haiku-4-5", cost="0.05", estimate=True)
        self._seed(day=self.today - timedelta(days=3), subkey="anthropic.claude-sonnet-4-6", cost="0.10", estimate=False)
        CostRefreshJob.objects.create(
            organization=self.org, app=self.app, status=CostRefreshJob.Status.SUCCEEDED,
            result={"rolling_24h_usd": "0.2500"},
        )

        context = panel.build_panel_context(app=self.app)

        self.assertTrue(context["has_cost"])
        self.assertEqual(len(context["chart"]["bars"]), panel.WINDOW_DAYS)
        self.assertEqual(len(context["legend"]), 2)
        self.assertEqual(context["window_total_display"], "$0.3500")
        self.assertEqual(context["rolling_24h_display"], "$0.2500")
        self.assertTrue(context["has_estimate"])
        # Sentinel rows (subkey="") must never appear in the per-model chart.
        self._seed(day=self.today, subkey="", cost="0", estimate=False)
        self.assertEqual(len(panel.build_panel_context(app=self.app)["legend"]), 2)

    def test_enqueue_refresh_is_deduped(self) -> None:
        panel.enqueue_refresh(app=self.app)
        panel.enqueue_refresh(app=self.app)
        active = CostRefreshJob.objects.filter(app=self.app, status__in=CostRefreshJob.ACTIVE_STATUSES)
        self.assertEqual(active.count(), 1)

    def test_build_panel_context_is_read_only(self) -> None:
        # Reading the panel must never enqueue — only a real page view does (via the view) — so the
        # read-only self-poll can't spawn an endless chain of refresh jobs.
        context = panel.build_panel_context(app=self.app)
        self.assertFalse(context["is_refreshing"])
        self.assertEqual(CostRefreshJob.objects.filter(app=self.app).count(), 0)

    def test_is_refreshing_tracks_active_job(self) -> None:
        CostRefreshJob.objects.create(organization=self.org, app=self.app, status=CostRefreshJob.Status.PENDING)
        self.assertTrue(panel.build_panel_context(app=self.app)["is_refreshing"])

    def test_chart_fragment_template_renders(self) -> None:
        self._seed(day=self.today, subkey="anthropic.claude-sonnet-4-6", cost="0.20", estimate=False)
        CostRefreshJob.objects.create(organization=self.org, app=self.app, status=CostRefreshJob.Status.PENDING)
        context = panel.build_panel_context(app=self.app)
        html = render_to_string("humanityrules_app/apps/_app_cost_chart.html", context)
        self.assertIn("Last 24h", html)
        self.assertIn(f"/apps/{self.app.slug}/cost-panel/?await=1", html)  # read-only self-poll url
        self.assertNotIn("{#", html)  # comments must be single-line, never rendered literally


class TestCostRefreshCoordination(_CostFixtureMixin, TestCase):
    """Enqueue and worker claims serialize refreshes by App."""

    def setUp(self) -> None:
        self._build_fixtures()

    def _create_job(self, app: App, status: str) -> CostRefreshJob:
        """Create a cost refresh job for a fixture App."""
        return CostRefreshJob.objects.create(
            organization=app.organization,
            app=app,
            status=status,
        )

    def test_enqueue_dedupes_running_job(self) -> None:
        self._create_job(app=self.app, status=CostRefreshJob.Status.RUNNING)

        panel.enqueue_refresh(app=self.app)

        self.assertEqual(CostRefreshJob.objects.filter(app=self.app).count(), 1)

    def test_enqueue_skips_app_pending_removal(self) -> None:
        self.app.job_status = App.JobStatus.REMOVAL_PENDING
        self.app.save(update_fields=["job_status", "updated_at"])

        panel.enqueue_refresh(app=self.app)

        self.assertFalse(CostRefreshJob.objects.filter(app=self.app).exists())

    def test_database_rejects_two_active_jobs_for_same_app(self) -> None:
        self._create_job(app=self.app, status=CostRefreshJob.Status.PENDING)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_job(app=self.app, status=CostRefreshJob.Status.RUNNING)

    def test_database_allows_new_job_after_terminal_job(self) -> None:
        self._create_job(app=self.app, status=CostRefreshJob.Status.SUCCEEDED)

        panel.enqueue_refresh(app=self.app)

        self.assertEqual(
            CostRefreshJob.objects.filter(app=self.app, status=CostRefreshJob.Status.PENDING).count(),
            1,
        )

    def test_worker_claims_pending_refresh(self) -> None:
        pending = self._create_job(app=self.app, status=CostRefreshJob.Status.PENDING)

        claimed = job_worker._claim_pending_cost_refresh(label="")

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, pending.id)
        self.assertEqual(claimed.status, CostRefreshJob.Status.RUNNING)

    def test_worker_claims_other_app_while_first_app_is_running(self) -> None:
        self._create_job(app=self.app, status=CostRefreshJob.Status.RUNNING)
        other_app = App.objects.create(
            organization=self.org,
            workspace=self.workspace,
            environment=self.env,
            repository=self.repository,
            name="Other Hermes",
            slug="other-hermes",
            build_strategy=App.BuildStrategy.DOCKERFILE,
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )
        pending = self._create_job(app=other_app, status=CostRefreshJob.Status.PENDING)

        claimed = job_worker._claim_pending_cost_refresh(label="")

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, pending.id)


class TestCostPanelView(_CostFixtureMixin, TestCase):
    """The HTMX panel endpoint: auth, fragment render, and refresh enqueue."""

    def setUp(self) -> None:
        self._build_fixtures()
        self.user = User.objects.create_user(
            username="vmendi", email="vmendi@example.com", password="pw", current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_panel_renders_and_enqueues_refresh(self) -> None:
        with patch("humanityrules_app.views.apps.abac_service.check_action", return_value=True):
            response = self.client.get(f"/apps/{self.app.slug}/cost-panel/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Last 24h", response.content)
        self.assertEqual(CostRefreshJob.objects.filter(app=self.app).count(), 1)

    def test_poll_request_does_not_enqueue(self) -> None:
        # The self-poll (?await=1) is read-only; it must never create a refresh job.
        with patch("humanityrules_app.views.apps.abac_service.check_action", return_value=True):
            response = self.client.get(f"/apps/{self.app.slug}/cost-panel/?await=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CostRefreshJob.objects.filter(app=self.app).count(), 0)

    def test_panel_denied_without_view_permission(self) -> None:
        with patch("humanityrules_app.views.apps.abac_service.check_action", return_value=False):
            response = self.client.get(f"/apps/{self.app.slug}/cost-panel/")
        self.assertEqual(response.status_code, 403)
