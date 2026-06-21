"""Bedrock cost source: CloudWatch Logs Insights over the shared invocation log group.

The log group ``/humr/bedrock-invocations`` is account+Region-shared across every HUMR
environment, so each query is filtered to this app's ECS task role(s). One Insights query does the
server-side aggregation (sum the four token buckets, grouped by day and model); we then price each
group via ``pricing.bedrock``. Billing is by **source Region** (where the logs land = the env's
region), so each environment is queried in its own region with its own assumed-role session.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from humanityrules_app.models import App, Deployment, Environment
from humanityrules_app.services.cost import bedrock_pricing
from humanityrules_app.services.cost.cost_source import DailyCostRow
from humanityrules_app.services.infra_customer import bedrock_logging_utils
from humanityrules_app.services.infra_customer import iam_utils

logger = logging.getLogger(__name__)

_QUERY_TIMEOUT_SECONDS = 60.0
_POLL_INTERVAL_SECONDS = 1.0

# Insights aggregation of the four disjoint token buckets. ``{group_by}`` is filled per call so the
# daily collect groups by day+model and the rolling-24h sum groups by model only.
_STATS_QUERY = (
    'filter identity.arn like "{role_name}"\n'
    "| stats sum(input.inputTokenCount) as input_tokens, "
    "sum(input.cacheReadInputTokenCount) as cache_read_tokens, "
    "sum(input.cacheWriteInputTokenCount) as cache_write_tokens, "
    "sum(output.outputTokenCount) as output_tokens, "
    "count(*) as invocations "
    "by {group_by}"
)


class _ModelAggregate:
    """Mutable per-(env, date, model) accumulator while folding Insights rows together."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_write_tokens = 0
        self.cache_read_tokens = 0
        self.invocations = 0
        self.cost_usd = Decimal(0)
        self.priced = True
        self.estimate = False


class BedrockCostSource:
    """``CostSource`` for Amazon Bedrock model-invocation cost."""

    key = "bedrock"

    def collect(self, app: App, start_date: date, end_date: date) -> list[DailyCostRow]:
        """Per-(environment, day, model) Bedrock cost over the inclusive UTC window."""
        start_seconds = int(datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc).timestamp())
        end_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc)
        end_seconds = int(end_dt.timestamp())

        aggregates: dict[tuple[UUID, date, str], _ModelAggregate] = {}
        for environment in _deployed_environments(app=app):
            grouped = self._query_environment(
                app=app,
                environment=environment,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                group_by="bin(1d) as day, modelId",
            )
            for row in grouped:
                row_date = _parse_bin_date(_bin_field(row=row))
                if row_date is None:
                    continue
                self._fold_row(
                    aggregates=aggregates,
                    key=(environment.id, row_date, bedrock_pricing.normalize_model_id(model_id=row["modelId"])[0]),
                    region=environment.aws_region,
                    row=row,
                )

        return [
            DailyCostRow(
                environment_id=env_id,
                date=row_date,
                subkey=model_key,
                cost_usd=agg.cost_usd,
                details=_details(agg=agg),
            )
            for (env_id, row_date, model_key), agg in aggregates.items()
        ]

    def rolling_24h_usd(self, app: App) -> Decimal:
        """Total Bedrock cost for ``app`` over exactly the trailing 24 hours (recomputed each refresh)."""
        now = datetime.now(tz=timezone.utc)
        start_seconds = int((now - timedelta(hours=24)).timestamp())
        end_seconds = int(now.timestamp())

        total = Decimal(0)
        for environment in _deployed_environments(app=app):
            grouped = self._query_environment(
                app=app,
                environment=environment,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                group_by="modelId",
            )
            for row in grouped:
                breakdown = _price_row(model_id=row["modelId"], region=environment.aws_region, row=row)
                total += breakdown.cost_usd
        return total

    def _query_environment(
        self, app: App, environment: Environment, start_seconds: int, end_seconds: int, group_by: str,
    ) -> list[dict]:
        """Run one Insights query in the environment's Region/account; return grouped rows as dicts."""
        role_name = _task_role_name(environment=environment, app=app)
        query_string = _STATS_QUERY.format(role_name=role_name, group_by=group_by)
        try:
            session = iam_utils._get_aws_session_for_environment(environment)
            client = session.client("logs")
            return _run_insights(
                client=client,
                log_group=bedrock_logging_utils.LOG_GROUP_NAME,
                query_string=query_string,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
        except Exception:
            logger.exception(
                "Bedrock cost query failed for app %(app)s in environment %(env)s",
                {"app": app.slug, "env": environment.slug},
            )
            return []

    def _fold_row(
        self, aggregates: dict[tuple[UUID, date, str], _ModelAggregate], key: tuple[UUID, date, str],
        region: str, row: dict,
    ) -> None:
        """Price one Insights row and fold it into the running aggregate for its (env, date, model)."""
        agg = aggregates.setdefault(key, _ModelAggregate())
        tokens = _row_tokens(row=row)
        agg.input_tokens += tokens["input"]
        agg.output_tokens += tokens["output"]
        agg.cache_write_tokens += tokens["cache_write"]
        agg.cache_read_tokens += tokens["cache_read"]
        agg.invocations += tokens["invocations"]

        breakdown = _price_row(model_id=row["modelId"], region=region, row=row)
        agg.cost_usd += breakdown.cost_usd
        agg.priced = agg.priced and breakdown.priced
        agg.estimate = agg.estimate or breakdown.estimate or not breakdown.priced


def _deployed_environments(app: App) -> list[Environment]:
    """Distinct environments the app has ever been deployed to (logs persist through teardown)."""
    environment_ids = Deployment.objects.filter(app=app).values_list("environment_id", flat=True).distinct()
    return list(Environment.objects.filter(id__in=list(environment_ids)).select_related("aws_account"))


def _task_role_name(environment: Environment, app: App) -> str:
    """The ECS task role name (forward mapping), matching how it is built on deploy. Truncated to 64."""
    return f"humr-{environment.slug}-{app.slug}-task-role"[:64]


def _run_insights(client, log_group: str, query_string: str, start_seconds: int, end_seconds: int) -> list[dict]:
    """Start an Insights query and poll to completion; return each result row as a ``{field: value}`` dict."""
    started = client.start_query(
        logGroupName=log_group, startTime=start_seconds, endTime=end_seconds, queryString=query_string,
    )
    query_id = started["queryId"]
    deadline = time.monotonic() + _QUERY_TIMEOUT_SECONDS
    while True:
        response = client.get_query_results(queryId=query_id)
        status = response["status"]
        if status == "Complete":
            return [{field["field"]: field["value"] for field in row} for row in response["results"]]
        if status in ("Failed", "Cancelled", "Timeout"):
            logger.error("Bedrock Insights query %(id)s ended with status %(status)s", {"id": query_id, "status": status})
            return []
        if time.monotonic() > deadline:
            try:
                client.stop_query(queryId=query_id)
            except Exception:
                pass
            logger.error("Bedrock Insights query %(id)s timed out after %(t)ss", {"id": query_id, "t": _QUERY_TIMEOUT_SECONDS})
            return []
        time.sleep(_POLL_INTERVAL_SECONDS)


def _row_tokens(row: dict) -> dict:
    """Coerce the token/count fields of an Insights row to ints (absent/null -> 0)."""
    return {
        "input": _as_int(row.get("input_tokens")),
        "output": _as_int(row.get("output_tokens")),
        "cache_write": _as_int(row.get("cache_write_tokens")),
        "cache_read": _as_int(row.get("cache_read_tokens")),
        "invocations": _as_int(row.get("invocations")),
    }


def _price_row(model_id: str, region: str, row: dict) -> bedrock_pricing.CostBreakdown:
    """Price a single Insights row's token sums for ``model_id`` in ``region``."""
    tokens = _row_tokens(row=row)
    return bedrock_pricing.cost_for(
        model_id=model_id,
        source_region=region,
        input_tokens=tokens["input"],
        output_tokens=tokens["output"],
        cache_write_tokens=tokens["cache_write"],
        cache_read_tokens=tokens["cache_read"],
    )


def _details(agg: _ModelAggregate) -> dict:
    """Build the ``details`` JSON stored on the daily row (token counts + pricing flags)."""
    return {
        "input_tokens": agg.input_tokens,
        "output_tokens": agg.output_tokens,
        "cache_write_tokens": agg.cache_write_tokens,
        "cache_read_tokens": agg.cache_read_tokens,
        "invocations": agg.invocations,
        "priced": agg.priced,
        "estimate": agg.estimate,
    }


def _as_int(value: str | None) -> int:
    """Parse an Insights numeric field (returned as a string) to int; 0 on absent/blank/bad."""
    if not value:
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _bin_field(row: dict) -> str | None:
    """The day-bin value, tolerating either the `as day` alias or the unaliased `bin(1d)` field name."""
    return row.get("day") or row.get("bin(1d)")


def _parse_bin_date(bin_value: str | None) -> date | None:
    """Parse the ``bin(1d)`` timestamp (``'YYYY-MM-DD HH:MM:SS.000'``) into a UTC date."""
    if not bin_value or len(bin_value) < 10:
        return None
    try:
        return date.fromisoformat(bin_value[:10])
    except ValueError:
        return None
