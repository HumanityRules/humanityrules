import copy
import logging
import uuid
from typing import Any

from django.utils import timezone
from policy_sentry.shared import iam_data as policy_sentry_iam_data

from .. import models
from .infra_customer import iam_utils

logger = logging.getLogger(__name__)

HUMR_APP_PERMISSIONS_POLICY_NAME = "doh-app-permissions"

CURATED_SERVICES = {"s3", "sqs", "dynamodb", "secretsmanager", "kms", "sns", "ssm", "logs", "ecs", "ecr", "lambda", "ses"}

ACCESS_LEVELS = ["Read", "Write", "List", "Tagging", "Permissions management"]

RESOURCE_PLACEHOLDERS = {
    "s3": "Select S3 bucket...",
    "sqs": "Select SQS queue...",
    "dynamodb": "Select DynamoDB table...",
    "secretsmanager": "Select secret...",
    "kms": "Select KMS key...",
    "sns": "Select SNS topic...",
    "ssm": "Select SSM parameter...",
    "logs": "Select log group...",
    "ses": "Select SES identity...",
    "ecr": "Select ECR repository...",
}


def new_statement_sid() -> str:
    """Generate a stable per-statement id used to target a statement in the UI and API."""
    return uuid.uuid4().hex


def _ensure_sids(statements: list[dict[str, Any]]) -> bool:
    """Assign a sid to any statement missing one. Returns True if any were added."""
    changed = False
    for statement in statements:
        if not statement.get("sid"):
            statement["sid"] = new_statement_sid()
            changed = True
    return changed


def ensure_statement_sids(app_permission_request) -> None:
    """Backfill stable sids onto the request's statements, persisting only if something changed."""
    statements = app_permission_request.statements or []
    if _ensure_sids(statements):
        app_permission_request.statements = statements
        app_permission_request.save(update_fields=["statements", "updated_at"])


def statements_equal(left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None) -> bool:
    """Compare two statement lists, ignoring the internal sid key (which never reaches AWS)."""
    def _strip(statements: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return [{k: v for k, v in stmt.items() if k != "sid"} for stmt in (statements or [])]
    return _strip(left) == _strip(right)


def get_or_create_app_permissions(app, environment) -> models.AppPermissions:
    """Get or create the AppPermissions baseline for an app+environment.

    If no AppPermissions exists yet, seeds from AWS (best-effort, empty list on failure).
    """
    try:
        return models.AppPermissions.objects.get(app=app, environment=environment)
    except models.AppPermissions.DoesNotExist:
        pass

    try:
        statements = iam_utils.read_app_permissions_policy(
            environment=environment, app=app, policy_name=HUMR_APP_PERMISSIONS_POLICY_NAME,
        )
    except Exception:
        logger.exception("Failed to seed AppPermissions from AWS for %s/%s", app.slug, environment.slug)
        statements = []

    return models.AppPermissions.objects.create(
        app=app,
        environment=environment,
        statements=statements,
    )


def get_or_create_draft(app, environment, user, app_permissions) -> models.AppPermissionRequest:
    """Find an existing DRAFT AppPermissionRequest for this app+environment, or create a new one."""
    existing = models.AppPermissionRequest.objects.filter(
        app=app,
        environment=environment,
        status=models.AppPermissionRequest.Status.DRAFT,
    ).order_by("-created_at").first()

    if existing:
        return existing

    statements = copy.deepcopy(app_permissions.statements or [])
    _ensure_sids(statements)
    return models.AppPermissionRequest.objects.create(
        app=app,
        environment=environment,
        statements=statements,
        status=models.AppPermissionRequest.Status.DRAFT,
        created_by=user,
    )


def update_statements(app_permission_request, *, action: str, service: str, statement_id: str, level: str, arn: str) -> str:
    """Mutate one statement (identified by statement_id) and save. Returns the affected statement's sid.

    `add_service` always appends a fresh statement — multiple statements may share a service,
    each holding a distinct access-level/resource scope. Every other action targets the
    statement whose sid matches `statement_id`.
    """
    statements = app_permission_request.statements or []
    _ensure_sids(statements)

    def _find(sid: str) -> dict[str, Any] | None:
        return next((stmt for stmt in statements if stmt.get("sid") == sid), None)

    affected_sid = statement_id

    if action == "add_service" and service:
        new_stmt = {"sid": new_statement_sid(), "service": service, "effect": "Allow", "access_levels": [], "resources": []}
        statements.append(new_stmt)
        affected_sid = new_stmt["sid"]

    elif action == "remove_service" and statement_id:
        statements = [s for s in statements if s.get("sid") != statement_id]

    elif action == "add_level" and statement_id and level:
        stmt = _find(statement_id)
        if stmt is not None and level not in stmt.get("access_levels", []):
            stmt.setdefault("access_levels", []).append(level)

    elif action == "remove_level" and statement_id and level:
        stmt = _find(statement_id)
        if stmt is not None:
            stmt["access_levels"] = [lvl for lvl in stmt.get("access_levels", []) if lvl != level]

    elif action == "add_resource" and statement_id and arn:
        stmt = _find(statement_id)
        if stmt is not None and arn not in stmt.get("resources", []):
            stmt.setdefault("resources", []).append(arn)

    elif action == "remove_resource" and statement_id and arn:
        stmt = _find(statement_id)
        if stmt is not None:
            stmt["resources"] = [r for r in stmt.get("resources", []) if r != arn]

    app_permission_request.statements = statements
    app_permission_request.save(update_fields=["statements", "updated_at"])
    return affected_sid


def apply_statement_action(app_permission_request, *, action: str, service: str, statement_id: str, level: str, arn: str, s3_prefix: str) -> str:
    """Mutate one statement (by statement_id), composing the S3 base-bucket ARN with its key prefix.

    S3 dropdown selections carry a base bucket ARN (arn:aws:s3:::bucket). On add,
    combine it with the prefix input to form the full resource ARN; on remove,
    drop every stored resource on that statement that belongs to the bucket. All other
    services and actions delegate to update_statements unchanged. Returns the affected sid.
    """
    if service == "s3" and action == "add_resource" and s3_prefix:
        arn = f"{arn}/{s3_prefix}"
    elif service == "s3" and action == "remove_resource":
        statements = app_permission_request.statements or []
        _ensure_sids(statements)
        for stmt in statements:
            if stmt.get("sid") == statement_id:
                stmt["resources"] = [r for r in stmt.get("resources", []) if not (r == arn or r.startswith(arn + "/"))]
        app_permission_request.statements = statements
        app_permission_request.save(update_fields=["statements", "updated_at"])
        return statement_id

    return update_statements(app_permission_request, action=action, service=service, statement_id=statement_id, level=level, arn=arn)


async def aupsert_statement(app_permission_request, service, access_levels, resources):
    """Merge access_levels into the `service` statement whose resource set matches `resources`.

    Keyed by (service, resources): if a statement for `service` already covers exactly this
    resource set, its access levels are merged in; otherwise a new statement is appended. This
    lets the agent express distinct scopes for one service (e.g. List on * vs Read on tableA/B).
    """
    statements = app_permission_request.statements or []
    _ensure_sids(statements)
    target_resources = set(resources)
    existing = next(
        (stmt for stmt in statements if stmt.get("service") == service and set(stmt.get("resources", [])) == target_resources),
        None,
    )
    if existing is None:
        existing = {"sid": new_statement_sid(), "service": service, "effect": "Allow", "access_levels": [], "resources": list(resources)}
        statements.append(existing)
    for level in access_levels:
        if level not in existing["access_levels"]:
            existing["access_levels"].append(level)
    app_permission_request.statements = statements
    await app_permission_request.asave(update_fields=["statements", "updated_at"])


def update_description(app_permission_request, description):
    """Replace the description field with the given text."""
    app_permission_request.description = description
    app_permission_request.save(update_fields=["description", "updated_at"])


async def amerge_description(app_permission_request, text):
    """Append text to the existing description, separated by a blank line."""
    existing = (app_permission_request.description or "").strip()
    if existing:
        app_permission_request.description = existing + "\n\n" + text
    else:
        app_permission_request.description = text
    await app_permission_request.asave(update_fields=["description", "updated_at"])


def approve(app_permission_request):
    """Set AppPermissionRequest status to APPROVED_PENDING_APPLY.

    The baseline (AppPermissions) is updated by the executor after the IAM policy is
    successfully applied, not here — so the baseline always reflects what's in AWS.
    """
    app_permission_request.status = models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY
    app_permission_request.save(update_fields=["status", "updated_at"])


def cancel(app_permission_request, app_permissions):
    """Reset the draft's statements back to the AppPermissions baseline."""
    statements = copy.deepcopy(app_permissions.statements or [])
    _ensure_sids(statements)
    app_permission_request.statements = statements
    app_permission_request.save(update_fields=["statements", "updated_at"])


def get_resources_for_services(environment, services):
    """Return cached AWS resources, fetching from AWS only for cache misses.

    Returns {"s3": [{"arn": "...", "label": "..."}, ...], ...}.
    """
    if not services:
        return {}

    cached = models.AwsResourceCache.objects.filter(
        environment=environment, service__in=services,
    )
    result = {entry.service: entry.resources for entry in cached}

    missing = [s for s in services if s not in result]
    if missing:
        fetched = iam_utils.list_resources_for_services(environment, missing)
        now = timezone.now()
        models.AwsResourceCache.objects.bulk_create(
            [
                models.AwsResourceCache(
                    environment=environment,
                    service=svc,
                    resources=fetched.get(svc, []),
                    fetched_at=now,
                )
                for svc in missing
            ],
            ignore_conflicts=True,
        )
        result.update(fetched)

    return result


def refresh_resources_cache(environment, services):
    """Delete and re-fetch cached resources for the given services.

    Returns {"s3": [{"arn": "...", "label": "..."}, ...], ...}.
    """
    if not services:
        return {}

    models.AwsResourceCache.objects.filter(
        environment=environment, service__in=services,
    ).delete()

    fetched = iam_utils.list_resources_for_services(environment, services)
    now = timezone.now()
    models.AwsResourceCache.objects.bulk_create(
        [
            models.AwsResourceCache(
                environment=environment,
                service=svc,
                resources=fetched.get(svc, []),
                fetched_at=now,
            )
            for svc in services
        ],
        ignore_conflicts=True,
    )
    return fetched


def _service_display_name(service: str) -> str:
    """Resolve an IAM service prefix to its human-readable name, falling back to the prefix."""
    try:
        return policy_sentry_iam_data.get_service_prefix_data(service).get("service_name", service)
    except Exception:
        return service


def _arn_resource_suffix(arn: str) -> str:
    """Return the resource portion of an ARN (e.g. a bucket or queue name)."""
    parts = arn.split(":", 5)
    return parts[5] if len(parts) == 6 and parts[5] else arn


def _resource_lines_for_summary(*, service: str, resources: list[str]) -> list[dict[str, str]]:
    """Human-readable resource lines for a service's permission-request summary."""
    if not resources or resources == ["*"]:
        return [{"label": "Resources", "value": "All resources (*)"}]
    if service == "s3":
        lines: list[dict[str, str]] = []
        for arn in resources:
            bucket, _, prefix = _arn_resource_suffix(arn).partition("/")
            lines.append({"label": "Bucket", "value": bucket})
            lines.append({"label": "Prefix", "value": prefix or "(entire bucket)"})
        return lines
    return [{"label": "Resources", "value": ", ".join(_arn_resource_suffix(arn) for arn in resources)}]


def summarize_statements_for_display(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a read-only summary of permission statements for the Security hub accordion.

    One summary per statement (no cross-statement merge), so distinct same-service scopes
    stay separate.
    """
    summaries: list[dict[str, Any]] = []
    for statement in statements:
        service = statement.get("service", "")
        if not service:
            continue
        summaries.append({
            "service": service,
            "display_name": _service_display_name(service),
            "access_levels": sorted(statement.get("access_levels", [])),
            "resource_lines": _resource_lines_for_summary(service=service, resources=statement.get("resources", [])),
        })
    return summaries


def build_service_group_data(
    *,
    sid: str,
    service: str,
    selected_levels: list[str] | set[str],
    resources: list[str],
    available_resources: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a render-ready dict for a single statement's group with access-level toggles."""
    display_name = _service_display_name(service)

    selected_set = set(selected_levels)
    access_levels = [{"name": level, "checked": level in selected_set} for level in ACCESS_LEVELS]

    # For S3, available resources are base bucket ARNs (e.g. arn:aws:s3:::my-bucket)
    # but stored resources include the prefix (e.g. arn:aws:s3:::my-bucket/data/*).
    # Use startswith matching so the bucket shows as "selected" when any prefixed resource exists.
    if service == "s3":
        marked_available = [
            {**r, "selected": any(res == r["arn"] or res.startswith(r["arn"] + "/") for res in resources)}
            for r in available_resources
        ]
    else:
        selected_arns = set(resources)
        marked_available = [{**r, "selected": r["arn"] in selected_arns} for r in available_resources]

    return {
        "sid": sid,
        "service": service,
        "display_name": display_name,
        "resources": resources,
        "available_resources": marked_available,
        "resource_placeholder": RESOURCE_PLACEHOLDERS.get(service, "Select resource..."),
        "access_levels": access_levels,
        "has_checked_levels": bool(selected_set),
        "selected_count": len(selected_set),
    }


def build_statement_groups(
    statements: list[dict[str, Any]],
    available_resources_by_service: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """Build one render-ready group per statement (no cross-statement merge), keyed by sid."""
    groups = []
    for statement in statements:
        service = statement.get("service", "")
        if not service:
            continue
        groups.append(build_service_group_data(
            sid=statement.get("sid", ""),
            service=service,
            selected_levels=statement.get("access_levels", []),
            resources=statement.get("resources", []),
            available_resources=available_resources_by_service.get(service, []),
        ))
    return groups


def fetch_available_resources(app_permission_request) -> dict[str, list[dict[str, str]]]:
    """Fetch available AWS resources for all services in a permission request (cache-backed)."""
    services = [stmt.get("service") for stmt in (app_permission_request.statements or []) if stmt.get("service")]
    if not services:
        return {}
    return get_resources_for_services(app_permission_request.environment, services)


def get_all_service_options() -> list[dict[str, str | bool]]:
    """Return the sorted list of all IAM services for the service picker."""
    iam_def = policy_sentry_iam_data.load_iam_definition()
    options = []
    for prefix, service_data in sorted(iam_def.items()):
        if not isinstance(service_data, dict):
            continue
        service_name = service_data.get("service_name", prefix)
        options.append({
            "value": prefix,
            "label": service_name,
            "is_curated": prefix in CURATED_SERVICES,
        })
    return options
