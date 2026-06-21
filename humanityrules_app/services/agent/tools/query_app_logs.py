"""
Tool for querying CloudWatch Logs for permission-related errors.

Searches the app's ECS log group for AccessDenied and authorization errors
to help identify missing IAM permissions at runtime.

Results are compacted: duplicate messages are grouped by error signature
(error code + API operation + resource ARN) so the agent sees each distinct
permission denial once, with a count and time range.
"""

import asyncio
import logging
import re
import time as time_module
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta

from humanityrules_app.models import AppPermissionRequest
from humanityrules_app.services.infra_customer.iam_utils import _get_aws_session_for_environment

logger = logging.getLogger(__name__)

# CloudWatch Logs filter pattern matching common AWS permission error messages.
# Each ?"term" is an OR branch with substring matching.
FILTER_PATTERN = (
    '?"AccessDenied" '                   # Catches AccessDenied, AccessDeniedException, (AccessDenied)
    '?"is not authorized to perform" '   # IAM policy denial message
    '?"Access Denied" '                  # S3 and general "Access Denied" text
    '?"UnauthorizedAccess" '             # GuardDuty / other services
    '?"AuthorizationError" '             # SNS, SQS
    '?"ExpiredToken"'                    # ExpiredToken, ExpiredTokenException
)

# Regex to extract the error signature from boto3-style error messages:
# "An error occurred (AccessDenied) when calling the GetObject operation"
_BOTO3_ERROR_RE = re.compile(
    r"\((\w+Denied\w*|UnauthorizedAccess|AuthorizationError|ExpiredToken\w*)\)"
    r"\s+when calling the (\w+) operation"
)

# Regex to extract resource ARN from IAM denial messages
_RESOURCE_ARN_RE = re.compile(r'on resource:\s*"?(arn:aws:[^"\s]+)"?')

# Regex to extract the IAM action from "is not authorized to perform: <action>"
_ACTION_RE = re.compile(r"is not authorized to perform:\s*([\w:]+)")


@dataclass
class CompactedLogGroup:
    """A group of deduplicated log events sharing the same error signature."""

    signature: str
    count: int
    first_seen: str
    last_seen: str
    sample_message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AppLogsResult:
    """Result of querying app logs for permission errors."""

    log_group: str
    groups: list[CompactedLogGroup]
    distinct_errors: int
    total_events: int
    truncated: bool

    def to_dict(self) -> dict:
        result = asdict(self)
        result["groups"] = [g.to_dict() for g in self.groups]
        return result


def _extract_signature(message: str) -> str:
    """Extract a stable grouping key from an error message.

    Tries to extract (error_code, operation, action, resource) from boto3-style
    messages. Falls back to the first 120 characters for unrecognized formats.
    """
    parts = []

    m = _BOTO3_ERROR_RE.search(message)
    if m:
        parts.append(f"{m.group(1)}:{m.group(2)}")

    m = _ACTION_RE.search(message)
    if m:
        parts.append(m.group(1))

    m = _RESOURCE_ARN_RE.search(message)
    if m:
        parts.append(m.group(1))

    if parts:
        return " | ".join(parts)

    # Fallback: truncated message as signature (strips variable suffixes)
    return message[:120]


MAX_EVENTS = 100   # Max number of events to fetch in the time window.
MAX_PAGES = 20     # FilterLogEvents returns empty pages while scanning; cap total API calls
MAX_GROUPS = 10    # Cap distinct groups returned to the agent


def _query_logs_sync(session, log_group: str, stream_prefix: str, start_time_ms: int, end_time_ms: int) -> AppLogsResult:
    """Synchronous CloudWatch Logs query with compaction (runs in thread pool)."""
    client = session.client("logs")

    kwargs = dict(
        logGroupName=log_group,
        logStreamNamePrefix=stream_prefix,
        startTime=start_time_ms,
        endTime=end_time_ms,
        filterPattern=FILTER_PATTERN,
        limit=MAX_EVENTS,
    )

    # Collect raw events
    raw_events: list[tuple[int, str]] = []  # (timestamp_ms, message)
    truncated = False

    try:
        for _ in range(MAX_PAGES):
            response = client.filter_log_events(**kwargs)

            for e in response.get("events", []):
                raw_events.append((e["timestamp"], e.get("message", "")))

            next_token = response.get("nextToken")
            if not next_token or len(raw_events) >= MAX_EVENTS:
                truncated = next_token is not None or len(raw_events) >= MAX_EVENTS
                break
            kwargs["nextToken"] = next_token
    except client.exceptions.ResourceNotFoundException:
        return AppLogsResult(log_group=log_group, groups=[], distinct_errors=0, total_events=0, truncated=False)

    raw_events = raw_events[:MAX_EVENTS]
    total_events = len(raw_events)

    # Group by signature
    groups: dict[str, dict] = {}
    for ts_ms, message in raw_events:
        sig = _extract_signature(message)
        if sig in groups:
            g = groups[sig]
            g["count"] += 1
            g["first_seen_ms"] = min(g["first_seen_ms"], ts_ms)
            g["last_seen_ms"] = max(g["last_seen_ms"], ts_ms)
        else:
            groups[sig] = {
                "count": 1,
                "first_seen_ms": ts_ms,
                "last_seen_ms": ts_ms,
                "sample_message": message[:2000],
            }

    # Sort by count descending, cap at MAX_GROUPS
    sorted_groups = sorted(groups.items(), key=lambda kv: kv[1]["count"], reverse=True)[:MAX_GROUPS]

    compacted = [
        CompactedLogGroup(
            signature=sig,
            count=g["count"],
            first_seen=datetime.fromtimestamp(g["first_seen_ms"] / 1000, tz=timezone.utc).isoformat(),
            last_seen=datetime.fromtimestamp(g["last_seen_ms"] / 1000, tz=timezone.utc).isoformat(),
            sample_message=g["sample_message"],
        )
        for sig, g in sorted_groups
    ]

    return AppLogsResult(
        log_group=log_group,
        groups=compacted,
        distinct_errors=len(groups),
        total_events=total_events,
        truncated=truncated,
    )


async def query_app_logs(
    apr: AppPermissionRequest,
    time_window_hours: int = 24,
) -> AppLogsResult:
    """Query CloudWatch Logs for permission errors in the app's log group.

    Args:
        apr: AppPermissionRequest with select_related app, environment, environment__aws_account.
        time_window_hours: How far back to search (1-168, default 24).

    Returns:
        AppLogsResult with compacted error groups.
    """
    time_window_hours = max(1, min(168, time_window_hours))

    env = apr.environment
    app = apr.app
    log_group = f"/devopshero/{env.slug}/ecs"
    stream_prefix = app.slug

    now = datetime.now(tz=timezone.utc)
    start_time_ms = int((now - timedelta(hours=time_window_hours)).timestamp() * 1000)
    end_time_ms = int(now.timestamp() * 1000)

    t1 = time_module.monotonic()
    session = await asyncio.to_thread(_get_aws_session_for_environment, env)
    
    result = await asyncio.to_thread(
        _query_logs_sync, session, log_group, stream_prefix, start_time_ms, end_time_ms,
    )
    t2 = time_module.monotonic()
    logger.info(
        "query_app_logs: filter_log_events took %.1fs (%d events, %d groups)",
        t2 - t1, result.total_events, result.distinct_errors,
    )
    
    return result
