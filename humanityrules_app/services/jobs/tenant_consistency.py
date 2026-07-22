"""Fail-fast tenant-consistency guards for background workers.

Workers have no `request.user` to scope by — they load a job row and trust the
related tenant-owned objects share an org. If a future job-creation site ever
produces an incoherent row, these guards stop the worker before it uses one
org's AWS credentials to operate on another org's resources.
"""

from humanityrules_app.models import App, AppPermissionRequest, Environment


class TenantConsistencyError(RuntimeError):
    """Raised when a worker's job row spans multiple organizations."""


def assert_apr_consistent(apr: AppPermissionRequest) -> None:
    """An AppPermissionRequest's app and the app's environment must share an org."""
    assert_app_owns_environment(app=apr.app, environment=apr.app.environment)


def assert_app_owns_environment(app: App, environment: Environment) -> None:
    """An app's environment must share the app's org."""
    env_org_id = environment.aws_account.organization_id
    if env_org_id != app.organization_id:
        raise TenantConsistencyError(
            f"App {app.id} (org {app.organization_id}) points at environment {environment.id} in org {env_org_id}"
        )
