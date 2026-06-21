"""Tests for account-scoped Bedrock model invocation logging setup."""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase

import humanityrules_app.services.infra_customer.bedrock_logging_utils as bedrock_logging_utils

ACCOUNT_ID = "123456789012"
ROLE_ARN = "arn:aws:iam::123456789012:role/devopshero-bedrock-logging-role"


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "PutModelInvocationLoggingConfiguration")


class _FakeAwsClients:
    """Stand-in for a boto3 session whose .client(name) returns per-service mocks."""

    def __init__(self) -> None:
        self.sts = MagicMock()
        self.sts.get_caller_identity.return_value = {"Account": ACCOUNT_ID}

        self.logs = MagicMock()
        self.logs.exceptions.ResourceAlreadyExistsException = type("ResourceAlreadyExistsException", (Exception,), {})

        self.iam = MagicMock()
        self.iam.exceptions.EntityAlreadyExistsException = type("EntityAlreadyExistsException", (Exception,), {})
        self.iam.create_role.return_value = {"Role": {"Arn": ROLE_ARN}}
        self.iam.get_role.return_value = {"Role": {"Arn": ROLE_ARN}}

        self.bedrock = MagicMock()

    def session(self) -> MagicMock:
        mapping = {"sts": self.sts, "logs": self.logs, "iam": self.iam, "bedrock": self.bedrock}
        session = MagicMock()
        session.client.side_effect = lambda name: mapping[name]
        return session


class TestEnsureInvocationLogging(SimpleTestCase):
    def test_creates_resources_and_enables_cloudwatch_with_data_disabled(self) -> None:
        clients = _FakeAwsClients()

        result = bedrock_logging_utils.ensure_invocation_logging(session=clients.session())

        self.assertTrue(result)
        clients.logs.create_log_group.assert_called_once_with(logGroupName=bedrock_logging_utils.LOG_GROUP_NAME)
        clients.logs.put_retention_policy.assert_called_once()
        clients.iam.create_role.assert_called_once()
        clients.iam.put_role_policy.assert_called_once()

        config = clients.bedrock.put_model_invocation_logging_configuration.call_args.kwargs["loggingConfig"]
        self.assertEqual(
            config["cloudWatchConfig"],
            {"logGroupName": bedrock_logging_utils.LOG_GROUP_NAME, "roleArn": ROLE_ARN},
        )
        self.assertFalse(config["textDataDeliveryEnabled"])
        self.assertFalse(config["imageDataDeliveryEnabled"])
        self.assertFalse(config["embeddingDataDeliveryEnabled"])
        self.assertFalse(config["videoDataDeliveryEnabled"])

    def test_reuses_existing_log_group(self) -> None:
        clients = _FakeAwsClients()
        clients.logs.create_log_group.side_effect = clients.logs.exceptions.ResourceAlreadyExistsException()

        result = bedrock_logging_utils.ensure_invocation_logging(session=clients.session())

        self.assertTrue(result)
        # Retention is still (re)applied even when the log group already existed.
        clients.logs.put_retention_policy.assert_called_once()

    def test_reuses_existing_role_via_get_role(self) -> None:
        clients = _FakeAwsClients()
        clients.iam.create_role.side_effect = clients.iam.exceptions.EntityAlreadyExistsException()

        result = bedrock_logging_utils.ensure_invocation_logging(session=clients.session())

        self.assertTrue(result)
        clients.iam.get_role.assert_called_once_with(RoleName=bedrock_logging_utils.ROLE_NAME)
        clients.iam.put_role_policy.assert_called_once()
        config = clients.bedrock.put_model_invocation_logging_configuration.call_args.kwargs["loggingConfig"]
        self.assertEqual(config["cloudWatchConfig"]["roleArn"], ROLE_ARN)

    @patch("humanityrules_app.services.infra_customer.bedrock_logging_utils.time.sleep")
    def test_retries_put_while_role_propagates_then_succeeds(self, mock_sleep: MagicMock) -> None:
        clients = _FakeAwsClients()
        clients.bedrock.put_model_invocation_logging_configuration.side_effect = [
            _client_error("ValidationException"),
            _client_error("ValidationException"),
            None,
        ]

        result = bedrock_logging_utils.ensure_invocation_logging(session=clients.session())

        self.assertTrue(result)
        self.assertEqual(clients.bedrock.put_model_invocation_logging_configuration.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("humanityrules_app.services.infra_customer.bedrock_logging_utils.time.sleep")
    def test_gives_up_after_max_attempts(self, mock_sleep: MagicMock) -> None:
        clients = _FakeAwsClients()
        clients.bedrock.put_model_invocation_logging_configuration.side_effect = _client_error("ValidationException")

        result = bedrock_logging_utils.ensure_invocation_logging(session=clients.session())

        self.assertFalse(result)
        self.assertEqual(
            clients.bedrock.put_model_invocation_logging_configuration.call_count,
            bedrock_logging_utils._ROLE_PROPAGATION_MAX_ATTEMPTS,
        )

    def test_does_not_retry_non_validation_put_errors(self) -> None:
        clients = _FakeAwsClients()
        clients.bedrock.put_model_invocation_logging_configuration.side_effect = _client_error("AccessDeniedException")

        result = bedrock_logging_utils.ensure_invocation_logging(session=clients.session())

        self.assertFalse(result)
        clients.bedrock.put_model_invocation_logging_configuration.assert_called_once()
