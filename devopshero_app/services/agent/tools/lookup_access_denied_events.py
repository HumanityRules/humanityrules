"""
Tool for querying CloudTrail for AccessDenied management events.

Searches CloudTrail LookupEvents for permission denials attributed to the
app's ECS task role, helping identify missing IAM permissions at runtime.
"""

import asyncio
import json
import logging
import time as time_module
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta

from devopshero_app.models import AppPermissionRequest
from devopshero_app.services.infra_customer.iam_utils import _get_aws_session_for_environment

logger = logging.getLogger(__name__)

ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedAccess", "Client.UnauthorizedAccess"}
MAX_PAGES = 10
EVENTS_PER_PAGE = 50


@dataclass
class CloudTrailEvent:
    """A single AccessDenied CloudTrail event."""

    event_time: str
    event_name: str
    event_source: str
    error_code: str
    error_message: str
    request_parameters: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AccessDeniedEventsResult:
    """Result of looking up AccessDenied events from CloudTrail."""

    task_role_name: str
    events: list[CloudTrailEvent]
    total_returned: int
    pages_fetched: int
    note: str

    def to_dict(self) -> dict:
        result = asdict(self)
        result["events"] = [e.to_dict() for e in self.events]
        return result


def _lookup_events_sync(session, task_role_name: str, start_time: datetime, end_time: datetime) -> AccessDeniedEventsResult:
    """Synchronous CloudTrail lookup (runs in thread pool)."""
    client = session.client("cloudtrail")

    matched_events: list[CloudTrailEvent] = []
    next_token = None
    pages_fetched = 0

    for _ in range(MAX_PAGES):
        kwargs = {
            "StartTime": start_time,
            "EndTime": end_time,
            "MaxResults": EVENTS_PER_PAGE,
        }
        if next_token:
            kwargs["NextToken"] = next_token

        response = client.lookup_events(**kwargs)
        pages_fetched += 1

        for event in response.get("Events", []):
            cloud_trail_event = event.get("CloudTrailEvent", "")
            try:
                detail = json.loads(cloud_trail_event) if cloud_trail_event else {}
            except (json.JSONDecodeError, TypeError):
                detail = {}

            error_code = detail.get("errorCode", "")
            if not any(code in error_code for code in ACCESS_DENIED_CODES):
                continue

            user_identity = detail.get("userIdentity", {})
            identity_arn = user_identity.get("arn", "")
            session_context = user_identity.get("sessionContext", {})
            session_arn = session_context.get("sessionIssuer", {}).get("arn", "")
            if task_role_name not in identity_arn and task_role_name not in session_arn:
                continue

            request_params = detail.get("requestParameters")
            request_params_str = json.dumps(request_params)[:500] if request_params else ""

            matched_events.append(CloudTrailEvent(
                event_time=event.get("EventTime", datetime.now(tz=timezone.utc)).isoformat() if isinstance(event.get("EventTime"), datetime) else str(event.get("EventTime", "")),
                event_name=detail.get("eventName", event.get("EventName", "")),
                event_source=detail.get("eventSource", event.get("EventSource", "")),
                error_code=error_code,
                error_message=detail.get("errorMessage", "")[:500],
                request_parameters=request_params_str,
            ))

        next_token = response.get("NextToken")
        if not next_token:
            break

        # Rate-limit between pages
        time_module.sleep(0.5)

    return AccessDeniedEventsResult(
        task_role_name=task_role_name,
        events=matched_events,
        total_returned=len(matched_events),
        pages_fetched=pages_fetched,
        note=(
            "Only covers management events (API calls like CreateBucket, PutQueuePolicy). "
            "Data events (S3 GetObject, DynamoDB PutItem, Lambda Invoke) require "
            "CloudTrail data event logging to be enabled separately."
        ),
    )


async def lookup_access_denied_events(
    apr: AppPermissionRequest,
    time_window_hours: int = 24,
) -> AccessDeniedEventsResult:
    """Look up CloudTrail AccessDenied events for the app's task role.

    Args:
        apr: AppPermissionRequest with select_related app, environment, environment__aws_account.
        time_window_hours: How far back to search (1-2160 / 90 days, default 24).

    Returns:
        AccessDeniedEventsResult with matching events.
    """
    time_window_hours = max(1, min(2160, time_window_hours))

    env = apr.environment
    app = apr.app
    task_role_name = f"doh-{env.slug}-{app.slug}-task-role"[:64]

    now = datetime.now(tz=timezone.utc)
    start_time = now - timedelta(hours=time_window_hours)

    session = await asyncio.to_thread(_get_aws_session_for_environment, env)

    return await asyncio.to_thread(
        _lookup_events_sync, session, task_role_name, start_time, now,
    )
