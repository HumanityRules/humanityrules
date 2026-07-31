"""Tests for credit rating: rate card selection and matching, per-event pricing, and the periodic pass.

The pass tests exercise the money rules that are expensive to get wrong: day
entries keyed by the rating date, accumulation in place across passes, amounts
re-derived from an exact running sum rather than rounded per event, refusals
that leave events unrated and retryable, idempotency, per-organization
transaction isolation, and the horizon that bounds the scan.
"""

import datetime
import io
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.services.billing import rates, rating


def _quantities(input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_write_tokens: int, reasoning_tokens: int) -> dict:
    """The llm quantity dict exactly as ingest stores it."""
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _unsaved_event(source: str, subkey: str, quantities: dict, occurred_at: datetime.datetime) -> models.BillingUsageEvent:
    """An in-memory event for pricing tests that need no database."""
    return models.BillingUsageEvent(
        app_id=uuid.uuid7(),
        app_slug="agent",
        owner_username="vmendi",
        source=source,
        subkey=subkey,
        quantities=quantities,
        occurred_at=occurred_at,
        idempotency_key=f"evt-{uuid.uuid7()}",
    )


def _card(version: str, effective_from: datetime.datetime, llm: dict) -> rates.RateCard:
    return rates.RateCard(version=version, effective_from=effective_from, llm=llm)


_FLAT_RATES = rates.LlmRates(
    input=Decimal("1000"),
    cache_read=Decimal("100"),
    output=Decimal("2000"),
    cache_write=Decimal("0"),
)


class TestRateCardSelection(SimpleTestCase):
    """rates_for picks the card that was in force when the usage happened."""

    def test_latest_card_effective_at_the_event_time_wins(self) -> None:
        cards = (
            _card(version="v1", effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC), llm={}),
            _card(version="v2", effective_from=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC), llm={}),
        )
        with patch.object(rates, "RATE_CARDS", cards):
            before = rates.rates_for(occurred_at=datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC))
            after = rates.rates_for(occurred_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC))
            on_the_boundary = rates.rates_for(occurred_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC))

        self.assertEqual(before.version, "v1")
        self.assertEqual(after.version, "v2")
        self.assertEqual(on_the_boundary.version, "v2")

    def test_usage_before_every_card_has_no_active_card(self) -> None:
        cards = (_card(version="v1", effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC), llm={}),)
        with patch.object(rates, "RATE_CARDS", cards):
            self.assertIsNone(rates.rates_for(occurred_at=datetime.datetime(2025, 12, 31, tzinfo=datetime.UTC)))

    def test_shipped_v1_covers_todays_usage(self) -> None:
        card = rates.rates_for(occurred_at=timezone.now())

        self.assertIsNotNone(card)
        self.assertEqual(card.version, "v1")
        self.assertEqual(
            sorted(card.llm),
            ["gpt-5.3-codex-spark", "gpt-5.4-mini", "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"],
        )


class TestModelFamilyMatching(SimpleTestCase):
    """Observed ids match curated family prefixes, with a tier-token guard on top."""

    def setUp(self) -> None:
        self.card = rates.rates_for(occurred_at=timezone.now())

    def test_exact_family_id_prices(self) -> None:
        lookup = rates.look_up_llm_rates(card=self.card, model_id="gpt-5.6-sol")

        self.assertEqual(lookup.family, "gpt-5.6-sol")
        self.assertEqual(lookup.rates.output, Decimal("4500"))

    def test_celestial_tier_variant_is_its_own_family(self) -> None:
        """The gpt-5.6 celestial variants are price tiers, not cosmetic codenames."""
        lookup = rates.look_up_llm_rates(card=self.card, model_id="gpt-5.6-luna")

        self.assertEqual(lookup.family, "gpt-5.6-luna")
        self.assertEqual(lookup.rates.output, Decimal("180"))

    def test_unlisted_gpt56_variant_refuses(self) -> None:
        """No bare gpt-5.6 family exists: an unseen tier must refuse, not price at another tier's rate."""
        lookup = rates.look_up_llm_rates(card=self.card, model_id="gpt-5.6-nova")

        self.assertIsNone(lookup.family)
        self.assertIsNone(lookup.rates)

    def test_dated_snapshot_prices_as_its_family(self) -> None:
        lookup = rates.look_up_llm_rates(card=self.card, model_id="gpt-5.4-mini-2026-03-17")

        self.assertEqual(lookup.family, "gpt-5.4-mini")
        self.assertEqual(lookup.rates.input, Decimal("110"))

    def test_longest_matching_family_wins(self) -> None:
        card = _card(
            version="test",
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            llm={"gpt-5.4": _FLAT_RATES, "gpt-5.4-mini": rates.LlmRates(
                input=Decimal("110"), cache_read=Decimal("11"), output=Decimal("675"), cache_write=Decimal("0"),
            )},
        )

        lookup = rates.look_up_llm_rates(card=card, model_id="gpt-5.4-mini-2026-03-17")

        self.assertEqual(lookup.family, "gpt-5.4-mini")
        self.assertEqual(lookup.rates.input, Decimal("110"))

    def test_tier_token_the_family_lacks_refuses(self) -> None:
        lookup = rates.look_up_llm_rates(card=self.card, model_id="gpt-5.5-mini")

        self.assertEqual(lookup.family, "gpt-5.5")
        self.assertIsNone(lookup.rates)
        self.assertIn("mini", lookup.refusal_reason)

    def test_tier_token_the_family_shares_prices(self) -> None:
        lookup = rates.look_up_llm_rates(card=self.card, model_id="gpt-5.4-mini-preview")

        self.assertEqual(lookup.family, "gpt-5.4-mini")
        self.assertIsNotNone(lookup.rates)

    def test_new_family_refuses(self) -> None:
        lookup = rates.look_up_llm_rates(card=self.card, model_id="gpt-6")

        self.assertIsNone(lookup.family)
        self.assertIsNone(lookup.rates)

    def test_next_version_of_a_priced_family_refuses(self) -> None:
        """gpt-5.55 extends gpt-5.5 as a string but is a different product."""
        lookup = rates.look_up_llm_rates(card=self.card, model_id="gpt-5.55")

        self.assertIsNone(lookup.family)
        self.assertIsNone(lookup.rates)

    def test_unpriced_tier_of_a_priced_family_refuses(self) -> None:
        lookup = rates.look_up_llm_rates(card=self.card, model_id="gpt-5.4-nano")

        self.assertIsNone(lookup.family)
        self.assertIsNone(lookup.rates)


class TestRateEvent(SimpleTestCase):
    """Per-event pricing: four disjoint buckets, Decimal throughout, refusal instead of a guess."""

    def setUp(self) -> None:
        self.occurred_at = timezone.now()

    def test_each_bucket_prices_at_its_own_rate(self) -> None:
        event = _unsaved_event(
            source="llm",
            subkey="gpt-5.6-sol",
            quantities=_quantities(
                input_tokens=200, output_tokens=350, cache_read_tokens=1000, cache_write_tokens=0, reasoning_tokens=80,
            ),
            occurred_at=self.occurred_at,
        )

        rated = rating.rate_event(event=event)

        # (200*750 + 1000*75 + 350*4500) / 1e6
        self.assertEqual(rated.credits, Decimal("1.8"))
        self.assertEqual(rated.family, "gpt-5.6-sol")
        self.assertEqual(rated.rate_card_version, "v1")
        self.assertEqual(rated.priced_tokens["input_tokens"], 200)
        self.assertNotIn("reasoning_tokens", rated.priced_tokens)

    def test_mini_family_prices_at_its_own_rates(self) -> None:
        event = _unsaved_event(
            source="llm",
            subkey="gpt-5.4-mini-2026-03-17",
            quantities=_quantities(
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_read_tokens=0,
                cache_write_tokens=0,
                reasoning_tokens=0,
            ),
            occurred_at=self.occurred_at,
        )

        rated = rating.rate_event(event=event)

        self.assertEqual(rated.credits, Decimal("785"))

    def test_reasoning_tokens_never_add_to_the_charge(self) -> None:
        without = _unsaved_event(
            source="llm",
            subkey="gpt-5.5",
            quantities=_quantities(
                input_tokens=100, output_tokens=1000, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
            ),
            occurred_at=self.occurred_at,
        )
        with_reasoning = _unsaved_event(
            source="llm",
            subkey="gpt-5.5",
            quantities=_quantities(
                input_tokens=100, output_tokens=1000, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=900,
            ),
            occurred_at=self.occurred_at,
        )

        self.assertEqual(rating.rate_event(event=without).credits, rating.rate_event(event=with_reasoning).credits)

    def test_cache_writes_are_recorded_but_cost_nothing(self) -> None:
        event = _unsaved_event(
            source="llm",
            subkey="gpt-5.6-sol",
            quantities=_quantities(
                input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=5_000_000, reasoning_tokens=0,
            ),
            occurred_at=self.occurred_at,
        )

        rated = rating.rate_event(event=event)

        self.assertEqual(rated.credits, Decimal(0))
        self.assertEqual(rated.priced_tokens["cache_write_tokens"], 5_000_000)

    def test_absent_quantity_key_reads_as_zero(self) -> None:
        event = _unsaved_event(
            source="llm",
            subkey="gpt-5.6-sol",
            quantities={"output_tokens": 1_000_000},
            occurred_at=self.occurred_at,
        )

        rated = rating.rate_event(event=event)

        self.assertEqual(rated.credits, Decimal("4500"))
        self.assertEqual(rated.priced_tokens["input_tokens"], 0)

    def test_unknown_source_refuses(self) -> None:
        event = _unsaved_event(
            source="tavily",
            subkey="search",
            quantities={"requests": 3},
            occurred_at=self.occurred_at,
        )

        self.assertIsNone(rating.rate_event(event=event))

    def test_unknown_family_refuses(self) -> None:
        event = _unsaved_event(
            source="llm",
            subkey="gpt-6-turbo",
            quantities=_quantities(
                input_tokens=100, output_tokens=100, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
            ),
            occurred_at=self.occurred_at,
        )

        self.assertIsNone(rating.rate_event(event=event))

    def test_usage_before_any_rate_card_refuses(self) -> None:
        event = _unsaved_event(
            source="llm",
            subkey="gpt-5.6-sol",
            quantities=_quantities(
                input_tokens=100, output_tokens=100, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
            ),
            occurred_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
        )

        self.assertIsNone(rating.rate_event(event=event))


class RatingPassTestBase(TestCase):
    """One organization with one app's worth of reported usage."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Rating Org", slug="rating-org")
        self.app_id = uuid.uuid7()
        self.event_counter = 0

    def make_event(
        self,
        organization: models.Organization,
        app_id: uuid.UUID,
        subkey: str,
        quantities: dict,
        occurred_at: datetime.datetime,
    ) -> models.BillingUsageEvent:
        """Persist one reported llm event."""
        self.event_counter += 1
        return models.BillingUsageEvent.objects.create(
            organization=organization,
            app_id=app_id,
            app_slug="rating-agent",
            owner_username="vmendi",
            source="llm",
            subkey=subkey,
            quantities=quantities,
            occurred_at=occurred_at,
            idempotency_key=f"evt-{self.event_counter}",
        )

    def make_standard_event(self, occurred_at: datetime.datetime) -> models.BillingUsageEvent:
        """A 1.8-credit gpt-5.6-sol event on the default org and app."""
        return self.make_event(
            organization=self.organization,
            app_id=self.app_id,
            subkey="gpt-5.6-sol",
            quantities=_quantities(
                input_tokens=200, output_tokens=350, cache_read_tokens=1000, cache_write_tokens=0, reasoning_tokens=80,
            ),
            occurred_at=occurred_at,
        )

    def run_pass(self) -> None:
        rating.rate_pending_events(now=timezone.now())

    def entries(self, organization: models.Organization) -> list[models.BillingLedgerEntry]:
        return list(models.BillingLedgerEntry.objects.filter(organization=organization).order_by("created_at"))

    def balance_credits(self, organization: models.Organization) -> Decimal:
        balance = models.BillingBalance.objects.filter(organization=organization).first()
        return balance.credits if balance is not None else Decimal(0)

    def assert_invariant_holds(self, organization: models.Organization) -> None:
        """balance == SUM(entries), the invariant every write path must preserve."""
        ledger_total = sum((entry.amount for entry in self.entries(organization=organization)), Decimal(0))
        self.assertEqual(self.balance_credits(organization=organization), ledger_total)


class TestRatingPassCharges(RatingPassTestBase):

    def test_pass_opens_one_day_entry_and_moves_the_balance(self) -> None:
        event = self.make_standard_event(occurred_at=timezone.now())

        self.run_pass()

        entries = self.entries(organization=self.organization)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.type, models.BillingLedgerEntry.Type.CHARGE)
        self.assertEqual(entry.amount, Decimal(-2))  # round(-1.8)
        self.assertEqual(Decimal(entry.metadata["exact_credits"]), Decimal("-1.8"))
        self.assertEqual(entry.metadata["rate_card_versions"], ["v1"])
        self.assertEqual(entry.metadata["event_count"], 1)
        self.assertEqual(entry.metadata["usage"]["gpt-5.6-sol"]["output_tokens"], 350)
        self.assertEqual(entry.metadata["app_id"], str(self.app_id))
        self.assertEqual(entry.metadata["app_slug"], "rating-agent")
        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(-2))
        self.assert_invariant_holds(organization=self.organization)

        event.refresh_from_db()
        self.assertIsNotNone(event.rated_at)

    def test_day_entry_is_keyed_by_the_rating_date_not_the_occurrence_date(self) -> None:
        self.make_standard_event(occurred_at=timezone.now() - datetime.timedelta(days=5))

        self.run_pass()

        today = timezone.now().astimezone(datetime.UTC).date()
        expected_key = rating.day_charge_idempotency_key(
            organization_id=self.organization.id, app_id=self.app_id, posting_date=today,
        )
        self.assertEqual(self.entries(organization=self.organization)[0].idempotency_key, expected_key)

    def test_rated_at_date_names_the_entry_the_event_landed_in(self) -> None:
        event = self.make_standard_event(occurred_at=timezone.now() - datetime.timedelta(days=3))

        self.run_pass()

        event.refresh_from_db()
        posting_date = event.rated_at.astimezone(datetime.UTC).date()
        expected_key = rating.day_charge_idempotency_key(
            organization_id=self.organization.id, app_id=event.app_id, posting_date=posting_date,
        )
        self.assertTrue(
            models.BillingLedgerEntry.objects.filter(
                organization=self.organization, idempotency_key=expected_key,
            ).exists()
        )

    def test_each_app_gets_its_own_day_entry(self) -> None:
        other_app_id = uuid.uuid7()
        self.make_standard_event(occurred_at=timezone.now())
        self.make_event(
            organization=self.organization,
            app_id=other_app_id,
            subkey="gpt-5.6-sol",
            quantities=_quantities(
                input_tokens=200, output_tokens=350, cache_read_tokens=1000, cache_write_tokens=0, reasoning_tokens=80,
            ),
            occurred_at=timezone.now(),
        )

        self.run_pass()

        entries = self.entries(organization=self.organization)
        self.assertEqual(len(entries), 2)
        self.assertEqual({entry.amount for entry in entries}, {Decimal(-2)})
        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(-4))
        self.assert_invariant_holds(organization=self.organization)

    def test_day_entries_leave_usage_event_null(self) -> None:
        self.make_standard_event(occurred_at=timezone.now())
        self.make_standard_event(occurred_at=timezone.now())

        self.run_pass()

        entry = self.entries(organization=self.organization)[0]
        self.assertIsNone(entry.usage_event_id)
        self.assertEqual(entry.metadata["event_count"], 2)


class TestRatingPassAccumulation(RatingPassTestBase):

    def test_later_pass_accumulates_into_the_same_day_entry(self) -> None:
        self.make_standard_event(occurred_at=timezone.now())
        self.run_pass()
        self.make_standard_event(occurred_at=timezone.now())

        self.run_pass()

        entries = self.entries(organization=self.organization)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].amount, Decimal(-4))  # round(-3.6)
        self.assertEqual(Decimal(entries[0].metadata["exact_credits"]), Decimal("-3.6"))
        self.assertEqual(entries[0].metadata["event_count"], 2)
        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(-4))
        self.assert_invariant_holds(organization=self.organization)

    def test_rerunning_a_pass_never_double_charges(self) -> None:
        self.make_standard_event(occurred_at=timezone.now())
        self.run_pass()

        for _repeat in range(3):
            self.run_pass()

        entries = self.entries(organization=self.organization)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].amount, Decimal(-2))
        self.assertEqual(entries[0].metadata["event_count"], 1)
        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(-2))

    def test_sub_credit_events_charge_from_the_exact_sum_not_per_event_rounding(self) -> None:
        """Ten 0.45-credit events cost 4 credits; rounding each to 0 would charge nothing."""
        amounts: list[Decimal] = []
        for _index in range(10):
            self.make_event(
                organization=self.organization,
                app_id=self.app_id,
                subkey="gpt-5.6-sol",
                quantities=_quantities(
                    input_tokens=0, output_tokens=100, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
                ),
                occurred_at=timezone.now(),
            )
            self.run_pass()
            amounts.append(self.entries(organization=self.organization)[0].amount)
            self.assert_invariant_holds(organization=self.organization)

        # The amount tracks round(exact) and wobbles by a credit intra-day.
        self.assertEqual(
            amounts,
            [Decimal(0), Decimal(-1), Decimal(-1), Decimal(-2), Decimal(-2),
             Decimal(-3), Decimal(-3), Decimal(-4), Decimal(-4), Decimal(-4)],
        )
        entry = self.entries(organization=self.organization)[0]
        self.assertEqual(Decimal(entry.metadata["exact_credits"]), Decimal("-4.5"))
        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(-4))

    def test_breakdown_credits_sum_to_the_exact_total(self) -> None:
        self.make_standard_event(occurred_at=timezone.now())
        self.make_event(
            organization=self.organization,
            app_id=self.app_id,
            subkey="gpt-5.4-mini-2026-03-17",
            quantities=_quantities(
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_read_tokens=0,
                cache_write_tokens=0,
                reasoning_tokens=0,
            ),
            occurred_at=timezone.now(),
        )

        self.run_pass()

        metadata = self.entries(organization=self.organization)[0].metadata
        breakdown_total = sum((Decimal(line["credits"]) for line in metadata["usage"].values()), Decimal(0))
        self.assertEqual(breakdown_total, Decimal(metadata["exact_credits"]))
        self.assertEqual(sorted(metadata["usage"]), ["gpt-5.4-mini", "gpt-5.6-sol"])


class TestRatingPassRefusals(RatingPassTestBase):

    def test_unknown_family_stays_unrated_and_is_picked_up_again(self) -> None:
        event = self.make_event(
            organization=self.organization,
            app_id=self.app_id,
            subkey="gpt-6-turbo",
            quantities=_quantities(
                input_tokens=1_000_000, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
            ),
            occurred_at=timezone.now(),
        )

        self.run_pass()
        self.run_pass()

        event.refresh_from_db()
        self.assertIsNone(event.rated_at)
        self.assertEqual(self.entries(organization=self.organization), [])
        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(0))

    def test_adding_the_family_prices_the_refused_event_retroactively(self) -> None:
        event = self.make_event(
            organization=self.organization,
            app_id=self.app_id,
            subkey="gpt-6-turbo",
            quantities=_quantities(
                input_tokens=1_000_000, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
            ),
            occurred_at=timezone.now(),
        )
        self.run_pass()

        widened = (_card(
            version="v-with-gpt-6",
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            llm={"gpt-6": _FLAT_RATES},
        ),)
        with patch.object(rates, "RATE_CARDS", widened):
            self.run_pass()

        event.refresh_from_db()
        self.assertIsNotNone(event.rated_at)
        entries = self.entries(organization=self.organization)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].amount, Decimal(-1000))
        self.assertEqual(entries[0].metadata["rate_card_versions"], ["v-with-gpt-6"])

    def test_refused_event_does_not_block_its_neighbours(self) -> None:
        refused = self.make_event(
            organization=self.organization,
            app_id=self.app_id,
            subkey="gpt-5.6-pro",
            quantities=_quantities(
                input_tokens=1_000_000, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
            ),
            occurred_at=timezone.now(),
        )
        priced = self.make_standard_event(occurred_at=timezone.now())

        self.run_pass()

        refused.refresh_from_db()
        priced.refresh_from_db()
        self.assertIsNone(refused.rated_at)
        self.assertIsNotNone(priced.rated_at)
        self.assertEqual(self.entries(organization=self.organization)[0].amount, Decimal(-2))


class TestRatingPassHorizon(RatingPassTestBase):

    def test_events_older_than_the_horizon_never_charge(self) -> None:
        stale = self.make_standard_event(occurred_at=timezone.now() - datetime.timedelta(days=31))

        self.run_pass()

        stale.refresh_from_db()
        self.assertIsNone(stale.rated_at)
        self.assertEqual(self.entries(organization=self.organization), [])

    def test_events_inside_the_horizon_still_charge(self) -> None:
        recent = self.make_standard_event(occurred_at=timezone.now() - datetime.timedelta(days=29))

        self.run_pass()

        recent.refresh_from_db()
        self.assertIsNotNone(recent.rated_at)
        self.assertEqual(self.entries(organization=self.organization)[0].amount, Decimal(-2))

    def test_a_stale_event_does_not_hold_back_a_fresh_one(self) -> None:
        stale = self.make_standard_event(occurred_at=timezone.now() - datetime.timedelta(days=45))
        fresh = self.make_standard_event(occurred_at=timezone.now())

        self.run_pass()

        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertIsNone(stale.rated_at)
        self.assertIsNotNone(fresh.rated_at)
        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(-2))


class TestRatingPassOrganizationIsolation(RatingPassTestBase):

    def setUp(self) -> None:
        super().setUp()
        self.other_organization = models.Organization.objects.create(name="Other Org", slug="other-org")
        self.other_app_id = uuid.uuid7()

    def make_other_org_event(self) -> models.BillingUsageEvent:
        return self.make_event(
            organization=self.other_organization,
            app_id=self.other_app_id,
            subkey="gpt-5.6-sol",
            quantities=_quantities(
                input_tokens=200, output_tokens=350, cache_read_tokens=1000, cache_write_tokens=0, reasoning_tokens=80,
            ),
            occurred_at=timezone.now(),
        )

    def test_each_organization_charges_only_its_own_usage(self) -> None:
        self.make_standard_event(occurred_at=timezone.now())
        self.make_other_org_event()

        self.run_pass()

        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(-2))
        self.assertEqual(self.balance_credits(organization=self.other_organization), Decimal(-2))
        self.assertEqual(len(self.entries(organization=self.organization)), 1)
        self.assertEqual(len(self.entries(organization=self.other_organization)), 1)

    def test_a_failing_organization_rolls_back_alone(self) -> None:
        failing_event = self.make_standard_event(occurred_at=timezone.now())
        healthy_event = self.make_other_org_event()
        original_upsert = rating._upsert_day_charge

        def _upsert_failing_for_first_org(
            organization_id: uuid.UUID,
            app_id: uuid.UUID,
            app_slug: str,
            posting_date: datetime.date,
            rated_events: list,
        ) -> Decimal:
            if organization_id == self.organization.id:
                raise RuntimeError("ledger write blew up")
            return original_upsert(
                organization_id=organization_id,
                app_id=app_id,
                app_slug=app_slug,
                posting_date=posting_date,
                rated_events=rated_events,
            )

        with patch.object(rating, "_upsert_day_charge", _upsert_failing_for_first_org):
            self.run_pass()

        failing_event.refresh_from_db()
        healthy_event.refresh_from_db()
        self.assertIsNone(failing_event.rated_at)
        self.assertEqual(self.entries(organization=self.organization), [])
        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(0))
        self.assertIsNotNone(healthy_event.rated_at)
        self.assertEqual(self.balance_credits(organization=self.other_organization), Decimal(-2))

        # And the failure is transient: the next pass charges what it rolled back.
        self.run_pass()

        failing_event.refresh_from_db()
        self.assertIsNotNone(failing_event.rated_at)
        self.assertEqual(self.balance_credits(organization=self.organization), Decimal(-2))
        self.assert_invariant_holds(organization=self.organization)


class TestBillingVerifyCommand(RatingPassTestBase):

    def call_verify(self, org_slug: str | None) -> str:
        out = io.StringIO()
        if org_slug is None:
            call_command("humr_billing_verify", stdout=out, stderr=io.StringIO())
        else:
            call_command("humr_billing_verify", org=org_slug, stdout=out, stderr=io.StringIO())
        return out.getvalue()

    def test_a_rated_organization_passes(self) -> None:
        self.make_standard_event(occurred_at=timezone.now())
        self.run_pass()

        output = self.call_verify(org_slug=None)

        self.assertIn("rating-org: balance -2 matches 1 ledger entry(ies)", output)
        self.assertIn("Billing invariants hold", output)

    def test_drifted_balance_fails(self) -> None:
        self.make_standard_event(occurred_at=timezone.now())
        self.run_pass()
        balance = models.BillingBalance.objects.get(organization=self.organization)
        balance.credits = Decimal(-1)
        balance.save(update_fields=["credits"])

        with self.assertRaises(CommandError) as raised:
            self.call_verify(org_slug=None)

        self.assertIn("1 billing invariant violation(s)", str(raised.exception))

    def test_ledger_without_a_balance_row_fails(self) -> None:
        self.make_standard_event(occurred_at=timezone.now())
        self.run_pass()
        models.BillingBalance.objects.filter(organization=self.organization).delete()

        with self.assertRaises(CommandError):
            self.call_verify(org_slug=None)

    def test_org_scope_checks_only_that_organization(self) -> None:
        other_organization = models.Organization.objects.create(name="Clean Org", slug="clean-org")
        self.make_standard_event(occurred_at=timezone.now())
        self.run_pass()
        balance = models.BillingBalance.objects.get(organization=self.organization)
        balance.credits = Decimal(99)
        balance.save(update_fields=["credits"])

        output = self.call_verify(org_slug=other_organization.slug)

        self.assertIn("clean-org: no billing activity", output)
        with self.assertRaises(CommandError):
            self.call_verify(org_slug=self.organization.slug)

    def test_unknown_org_slug_fails(self) -> None:
        with self.assertRaises(CommandError):
            self.call_verify(org_slug="nope")

    def test_day_charge_mutated_after_its_day_closed_fails(self) -> None:
        self.make_standard_event(occurred_at=timezone.now())
        self.run_pass()
        # Rebadge the entry's key three days back; its auto_now updated_at stays
        # today, which is exactly what a closed-day mutation looks like.
        entry = models.BillingLedgerEntry.objects.get(organization=self.organization)
        stale_key = rating.day_charge_idempotency_key(
            organization_id=self.organization.id,
            app_id=self.app_id,
            posting_date=(timezone.now() - datetime.timedelta(days=3)).date(),
        )
        models.BillingLedgerEntry.objects.filter(organization=self.organization, id=entry.id).update(
            idempotency_key=stale_key,
        )

        with self.assertRaises(CommandError) as raised:
            self.call_verify(org_slug=None)

        self.assertIn("1 billing invariant violation(s)", str(raised.exception))
