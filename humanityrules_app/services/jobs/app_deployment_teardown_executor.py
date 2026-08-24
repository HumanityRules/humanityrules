"""
App infrastructure teardown.

Deletes an app's CloudFormation stack. There is no standalone teardown job: the only
callers are flows that tear down inline as part of a larger attempt — app removal
(`app_remove_executor`) and environment teardown (`environment_teardown_executor`) —
and both manage their own App state transitions around this call.
"""

import logging

from django.conf import settings

from humanityrules_app import models
from humanityrules_app.services import infra_customer

logger = logging.getLogger(__name__)


def _get_aws_session(environment: models.Environment):
    """Get an AWS session with assumed role credentials for the target account."""
    aws_account = environment.aws_account

    return infra_customer.iam_utils.get_assumed_role_session(
        access_key=settings.HUMR_AWS_ACCESS_KEY,
        secret_key=settings.HUMR_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=environment.aws_region,
    )


def teardown_infra(app: models.App) -> bool:
    """Delete the app's stack without touching App job state. Returns success."""
    session = _get_aws_session(environment=app.environment)
    return infra_customer.deploy_app.teardown(
        session=session,
        env_slug=app.environment.slug,
        app_name=app.slug,
    )
