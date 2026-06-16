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

    def test_merges_statements_for_same_service(self) -> None:
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

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["access_levels"], ["Read", "Write"])
        self.assertEqual(len(summaries[0]["resource_lines"]), 4)

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
