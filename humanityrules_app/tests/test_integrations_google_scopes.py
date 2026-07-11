"""Tests for the Google product/capability model: parsing, projection, narrowing detection."""

from django.test import SimpleTestCase

from humanityrules_app.views.integrations import provider_google


class TestParseProductsParam(SimpleTestCase):

    def test_absent_param_defaults_to_all_read(self) -> None:
        selection = provider_google.parse_products_param(raw=None)
        self.assertEqual(selection, {product: "read" for product in provider_google.GOOGLE_PRODUCTS})

    def test_valid_selection_fills_unlisted_products_as_off(self) -> None:
        selection = provider_google.parse_products_param(raw="gmail:write,calendar:read")
        self.assertEqual(selection["gmail"], "write")
        self.assertEqual(selection["calendar"], "read")
        self.assertEqual(selection["drive"], "off")

    def test_whitespace_around_entries_is_tolerated(self) -> None:
        selection = provider_google.parse_products_param(raw="gmail:read, calendar:read")
        self.assertIsNotNone(selection)
        self.assertEqual(selection["calendar"], "read")

    def test_invalid_values_return_none(self) -> None:
        for invalid in ("", "gmail", "gmail:", ":read", "gmail:admin", "nope:read",
                        "gmail:read,gmail:read", "gmail:off", "GMAIL:read"):
            with self.subTest(raw=invalid):
                self.assertIsNone(provider_google.parse_products_param(raw=invalid))

    def test_round_trips_through_serialize(self) -> None:
        selection = provider_google.parse_products_param(raw="gmail:write,docs:read")
        raw = provider_google.serialize_selection(selection=selection)
        self.assertEqual(provider_google.parse_products_param(raw=raw), selection)


class TestScopesForSelection(SimpleTestCase):

    def test_base_scopes_always_present(self) -> None:
        selection = provider_google.parse_products_param(raw="gmail:read")
        scopes = provider_google.scopes_for_selection(selection=selection)
        self.assertIn("openid", scopes)
        self.assertIn("email", scopes)

    def test_write_replaces_read_scope_for_gmail(self) -> None:
        selection = provider_google.parse_products_param(raw="gmail:write")
        scopes = provider_google.scopes_for_selection(selection=selection)
        self.assertIn("https://www.googleapis.com/auth/gmail.modify", scopes)
        self.assertNotIn("https://www.googleapis.com/auth/gmail.readonly", scopes)

    def test_calendar_write_keeps_readonly_and_adds_events(self) -> None:
        selection = provider_google.parse_products_param(raw="calendar:write")
        scopes = provider_google.scopes_for_selection(selection=selection)
        self.assertIn("https://www.googleapis.com/auth/calendar.readonly", scopes)
        self.assertIn("https://www.googleapis.com/auth/calendar.events", scopes)
        self.assertNotIn("https://www.googleapis.com/auth/calendar", scopes)


class TestLevelsFromScopeString(SimpleTestCase):

    def test_projects_read_and_write_levels(self) -> None:
        levels = provider_google.levels_from_scope_string(scope=(
            "https://www.googleapis.com/auth/gmail.modify "
            "https://www.googleapis.com/auth/drive.readonly openid email"
        ))
        self.assertEqual(levels["gmail"], "write")
        self.assertEqual(levels["drive"], "read")
        self.assertEqual(levels["calendar"], "off")

    def test_write_marker_wins_when_both_read_and_write_present(self) -> None:
        # Incremental auth can leave both behind.
        levels = provider_google.levels_from_scope_string(scope=(
            "https://www.googleapis.com/auth/gmail.readonly "
            "https://www.googleapis.com/auth/gmail.modify"
        ))
        self.assertEqual(levels["gmail"], "write")

    def test_broader_scopes_granted_elsewhere_classify_as_write(self) -> None:
        # Project-wide grant merging can surface scopes we never request.
        levels = provider_google.levels_from_scope_string(scope=(
            "https://mail.google.com/ https://www.googleapis.com/auth/calendar"
        ))
        self.assertEqual(levels["gmail"], "write")
        self.assertEqual(levels["calendar"], "write")

    def test_unknown_scopes_are_ignored(self) -> None:
        levels = provider_google.levels_from_scope_string(scope="https://www.googleapis.com/auth/somethingnew")
        self.assertEqual(set(levels.values()), {"off"})

    def test_empty_scope_is_all_off(self) -> None:
        levels = provider_google.levels_from_scope_string(scope="")
        self.assertEqual(set(levels.values()), {"off"})


class TestIsNarrowing(SimpleTestCase):

    def _levels(self, **overrides: str) -> dict[str, str]:
        levels = {product: "off" for product in provider_google.GOOGLE_PRODUCTS}
        levels.update(overrides)
        return levels

    def test_upgrade_read_to_write_is_not_narrowing(self) -> None:
        self.assertFalse(provider_google.is_narrowing(
            requested=self._levels(gmail="write"),
            granted=self._levels(gmail="read"),
        ))

    def test_downgrade_write_to_read_is_narrowing(self) -> None:
        self.assertTrue(provider_google.is_narrowing(
            requested=self._levels(gmail="read"),
            granted=self._levels(gmail="write"),
        ))

    def test_dropping_a_product_is_narrowing(self) -> None:
        self.assertTrue(provider_google.is_narrowing(
            requested=self._levels(gmail="read"),
            granted=self._levels(gmail="read", calendar="read"),
        ))

    def test_simultaneous_add_and_remove_is_narrowing(self) -> None:
        self.assertTrue(provider_google.is_narrowing(
            requested=self._levels(gmail="off", drive="write"),
            granted=self._levels(gmail="read"),
        ))

    def test_identical_selection_is_not_narrowing(self) -> None:
        self.assertFalse(provider_google.is_narrowing(
            requested=self._levels(gmail="read"),
            granted=self._levels(gmail="read"),
        ))


class TestImpliedLevels(SimpleTestCase):

    def _levels(self, **overrides: str) -> dict[str, str]:
        levels = {product: "off" for product in provider_google.GOOGLE_PRODUCTS}
        levels.update(overrides)
        return levels

    def test_drive_read_floors_docs_and_sheets_at_read(self) -> None:
        implied = provider_google.implied_levels(levels=self._levels(drive="read"))
        self.assertEqual(implied["docs"], "read")
        self.assertEqual(implied["sheets"], "read")

    def test_drive_write_floors_docs_and_sheets_at_write(self) -> None:
        implied = provider_google.implied_levels(levels=self._levels(drive="write", docs="read"))
        self.assertEqual(implied["docs"], "write")
        self.assertEqual(implied["sheets"], "write")

    def test_higher_own_level_is_kept(self) -> None:
        implied = provider_google.implied_levels(levels=self._levels(drive="read", docs="write"))
        self.assertEqual(implied["docs"], "write")

    def test_gmail_and_others_are_untouched(self) -> None:
        implied = provider_google.implied_levels(levels=self._levels(drive="write"))
        self.assertEqual(implied["gmail"], "off")
        self.assertEqual(implied["calendar"], "off")
        self.assertEqual(implied["contacts"], "off")

    def test_scope_projection_applies_the_closure(self) -> None:
        # A bare drive.readonly grant must NOT present Docs/Sheets as off.
        levels = provider_google.levels_from_scope_string(
            scope="https://www.googleapis.com/auth/drive.readonly",
        )
        self.assertEqual(levels["drive"], "read")
        self.assertEqual(levels["docs"], "read")
        self.assertEqual(levels["sheets"], "read")
