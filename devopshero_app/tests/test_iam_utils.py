"""Tests for iam_utils policy-document construction."""

from django.test import SimpleTestCase

from devopshero_app.services.infra_customer import iam_utils


class TestBuildIamPolicyS3WholeBucket(SimpleTestCase):
    def test_bare_bucket_grant_also_covers_objects(self) -> None:
        doc = iam_utils._build_iam_policy_document([
            {"service": "s3", "access_levels": ["Read"], "resources": ["arn:aws:s3:::my-bucket"]},
        ])

        s3_stmt = doc["Statement"][0]
        self.assertIn("arn:aws:s3:::my-bucket", s3_stmt["Resource"])
        self.assertIn("arn:aws:s3:::my-bucket/*", s3_stmt["Resource"])
        # The object action that was silently dead before now has a matching ARN.
        self.assertIn("s3:GetObject", s3_stmt["Action"])

    def test_prefixed_resource_is_left_untouched(self) -> None:
        doc = iam_utils._build_iam_policy_document([
            {"service": "s3", "access_levels": ["Read"], "resources": ["arn:aws:s3:::my-bucket/data"]},
        ])

        self.assertEqual(doc["Statement"][0]["Resource"], ["arn:aws:s3:::my-bucket/data"])

    def test_non_s3_service_not_expanded(self) -> None:
        doc = iam_utils._build_iam_policy_document([
            {"service": "dynamodb", "access_levels": ["Read"], "resources": ["arn:aws:dynamodb:us-east-1:111122223333:table/t"]},
        ])

        self.assertEqual(doc["Statement"][0]["Resource"], ["arn:aws:dynamodb:us-east-1:111122223333:table/t"])
