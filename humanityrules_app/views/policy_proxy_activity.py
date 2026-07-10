"""Runtime activity reports from policy proxies inside customer environments."""

import datetime
import json

from django.db import models as django_models
from django.http import HttpRequest, JsonResponse
from django.utils import dateparse, timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from humanityrules_app import models
from . import env_bearer_auth

MAX_FUTURE_CLOCK_SKEW = datetime.timedelta(minutes=5)


@csrf_exempt
@require_POST
def policy_proxy_activity(request: HttpRequest) -> JsonResponse:
    """Record the latest authorized traffic observed by a policy proxy."""
    raw_token = env_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return JsonResponse({"error": "missing bearer token"}, status=401)

    environment = env_bearer_auth.resolve_env_from_token(raw_token=raw_token)
    if environment is None:
        return JsonResponse({"error": "invalid bearer token"}, status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "JSON object body is required"}, status=400)

    app_id = payload.get("app_id")
    observed_at_value = payload.get("observed_at")
    if not isinstance(app_id, str) or not app_id:
        return JsonResponse({"error": "app_id is required"}, status=400)
    if not isinstance(observed_at_value, str):
        return JsonResponse({"error": "observed_at is required"}, status=400)

    observed_at = dateparse.parse_datetime(observed_at_value)
    if observed_at is None or timezone.is_naive(observed_at):
        return JsonResponse({"error": "observed_at must be an ISO 8601 timestamp with a timezone"}, status=400)
    now = timezone.now()
    if observed_at > now + MAX_FUTURE_CLOCK_SKEW:
        return JsonResponse({"error": "observed_at is too far in the future"}, status=400)

    organization = environment.aws_account.organization
    app = models.App.objects.filter(
        organization=organization,
        slug=app_id,
        blueprints__environment=environment,
    ).first()
    if app is None:
        return JsonResponse({"error": "app not found in environment"}, status=404)

    activity, created = models.AppEnvironmentActivity.objects.get_or_create(
        organization=organization,
        app=app,
        environment=environment,
        defaults={"last_policy_proxy_activity_at": observed_at},
    )
    if not created:
        models.AppEnvironmentActivity.objects.filter(
            organization=organization,
            id=activity.id,
        ).update(
            last_policy_proxy_activity_at=django_models.functions.Greatest(
                "last_policy_proxy_activity_at",
                django_models.Value(observed_at),
            ),
            updated_at=now,
        )

    return JsonResponse({"ok": True})
