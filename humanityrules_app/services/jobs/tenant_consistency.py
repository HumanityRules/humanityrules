"""Fail-fast tenant-consistency guards for background workers.

Workers have no `request.user` to scope by — they load a job row and trust the
related tenant-owned objects share an org. If a future job-creation site ever
produces an incoherent row, these guards stop the worker before it uses one
org's AWS credentials to operate on another org's resources.
"""

from humanityrules_app.models import App, AppPermissionRequest, Deployment, Environment


class TenantConsistencyError(RuntimeError):
    """Raised when a worker's job row spans multiple organizations."""


def assert_deployment_consistent(deployment: Deployment) -> None:
    """All tenant-owned objects on a Deployment must share an org."""
    app_org_id = deployment.app.organization_id
    env_org_id = deployment.environment.aws_account.organization_id
    if app_org_id != env_org_id:
        raise TenantConsistencyError(
            f"Deployment {deployment.id}: app org={app_org_id} != env org={env_org_id}"
        )
    blueprint = deployment.blueprint
    if blueprint is None:
        return
    if blueprint.app.organization_id != app_org_id:
        raise TenantConsistencyError(
            f"Deployment {deployment.id}: blueprint.app org={blueprint.app.organization_id} "
            f"!= deployment.app org={app_org_id}"
        )
    if blueprint.environment.aws_account.organization_id != env_org_id:
        raise TenantConsistencyError(
            f"Deployment {deployment.id}: blueprint.environment org="
            f"{blueprint.environment.aws_account.organization_id} != deployment.environment org={env_org_id}"
        )


def assert_apr_consistent(apr: AppPermissionRequest) -> None:
    """An AppPermissionRequest's app and environment must share an org."""
    app_org_id = apr.app.organization_id
    env_org_id = apr.environment.aws_account.organization_id
    if app_org_id != env_org_id:
        raise TenantConsistencyError(
            f"AppPermissionRequest {apr.id}: app org={app_org_id} != env org={env_org_id}"
        )


def assert_app_owns_environment(app: App, environment: Environment) -> None:
    """An environment iterated via an app's blueprints must share the app's org."""
    env_org_id = environment.aws_account.organization_id
    if env_org_id != app.organization_id:
        raise TenantConsistencyError(
            f"App {app.id} (org {app.organization_id}) has a blueprint pointing at "
            f"environment {environment.id} in org {env_org_id}"
        )
