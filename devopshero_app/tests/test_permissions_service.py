"""Tests for permissions_service helpers."""

from django.test import SimpleTestCase

from devopshero_app.services import permissions_service


class TestSummarizeStatementsForDisplay(SimpleTestCase):
    def test_s3_shows_bucket_and_prefix(self) -> None:
        summaries = permissions_service.summarize_statements_for_display([
            {
                "service": "s3",
                "access_levels": ["Read", "Write"],
                "resources": ["arn:aws:s3:::my-bucket/data/*"],
            },
        ])

        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary["service"], "s3")
        self.assertEqual(summary["access_levels"], ["Read", "Write"])
        self.assertEqual(summary["resource_lines"], [
            {"label": "Bucket", "value": "my-bucket"},
            {"label": "Prefix", "value": "data/*"},
        ])

    def test_s3_bucket_without_prefix(self) -> None:
        summaries = permissions_service.summarize_statements_for_display([
            {
                "service": "s3",
                "access_levels": ["List"],
                "resources": ["arn:aws:s3:::my-bucket"],
            },
        ])

        self.assertEqual(summaries[0]["resource_lines"], [
            {"label": "Bucket", "value": "my-bucket"},
            {"label": "Prefix", "value": "(entire bucket)"},
        ])

    def test_empty_resources_shows_wildcard(self) -> None:
        summaries = permissions_service.summarize_statements_for_display([
            {
                "service": "sqs",
                "access_levels": ["Read"],
                "resources": [],
            },
        ])

        self.assertEqual(summaries[0]["resource_lines"], [
            {"label": "Resources", "value": "All resources (*)"},
        ])

    def test_does_not_merge_statements_for_same_service(self) -> None:
        # Distinct same-service scopes must stay separate so the summary mirrors the
        # actual IAM statements (one card per statement), not a flattened union.
        summaries = permissions_service.summarize_statements_for_display([
            {
                "service": "s3",
                "access_levels": ["Read"],
                "resources": ["arn:aws:s3:::bucket-a/*"],
            },
            {
                "service": "s3",
                "access_levels": ["Write"],
                "resources": ["arn:aws:s3:::bucket-b/logs/*"],
            },
        ])

        self.assertEqual(len(summaries), 2)
        self.assertEqual(summaries[0]["access_levels"], ["Read"])
        self.assertEqual(summaries[1]["access_levels"], ["Write"])

    def test_non_s3_uses_short_resource_names(self) -> None:
        summaries = permissions_service.summarize_statements_for_display([
            {
                "service": "sqs",
                "access_levels": ["Read"],
                "resources": ["arn:aws:sqs:us-east-1:123456789012:my-queue"],
            },
        ])

        self.assertEqual(summaries[0]["resource_lines"], [
            {"label": "Resources", "value": "my-queue"},
        ])


class TestBuildStatementGroups(SimpleTestCase):
    def test_one_group_per_statement_keeping_distinct_same_service_scopes(self) -> None:
        groups = permissions_service.build_statement_groups(
            [
                {"sid": "a1", "service": "dynamodb", "access_levels": ["List"], "resources": ["*"]},
                {"sid": "b2", "service": "dynamodb", "access_levels": ["Read"], "resources": ["arn:aws:dynamodb:us-east-1:111:table/Orders"]},
            ],
            {},
        )

        self.assertEqual([g["sid"] for g in groups], ["a1", "b2"])
        self.assertEqual([g["service"] for g in groups], ["dynamodb", "dynamodb"])
        list_group = next(g for g in groups if g["sid"] == "a1")
        self.assertTrue(next(lvl for lvl in list_group["access_levels"] if lvl["name"] == "List")["checked"])
        self.assertFalse(next(lvl for lvl in list_group["access_levels"] if lvl["name"] == "Read")["checked"])


class TestStatementsEqual(SimpleTestCase):
    def test_ignores_sid(self) -> None:
        baseline = [{"service": "s3", "effect": "Allow", "access_levels": ["Read"], "resources": []}]
        draft = [{"sid": "xyz", "service": "s3", "effect": "Allow", "access_levels": ["Read"], "resources": []}]
        self.assertTrue(permissions_service.statements_equal(draft, baseline))

    def test_detects_real_changes(self) -> None:
        baseline = [{"service": "s3", "access_levels": ["Read"], "resources": []}]
        draft = [{"sid": "xyz", "service": "s3", "access_levels": ["Read", "Write"], "resources": []}]
        self.assertFalse(permissions_service.statements_equal(draft, baseline))

    def test_handles_none(self) -> None:
        self.assertTrue(permissions_service.statements_equal(None, []))
