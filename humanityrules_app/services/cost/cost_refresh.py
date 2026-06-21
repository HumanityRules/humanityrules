"""Source-agnostic cost refresh: window selection, freeze, idempotent upsert, and rolling-24h.

This layer never sees a token, a model id, or a dollar rate — it runs every registered ``CostSource``
over a recompute window and folds the returned ``DailyCostRow`` records into ``AppDailyCost``.

Window/freeze (see ``docs/app_cost_tracking_design.md``):
- The window is clamped to the log retention (``bedrock_logging_utils.LOG_GROUP_RETENTION_DAYS``) — a day
  that has aged out can never be re-queried, so the table is the durable history.
- We always recompute **today** and **yesterday** (absorbs Bedrock delivery lag) plus any non-final day
  back to the last frozen day (covers gaps from missed runs), never before the retention floor.
- After upserting, days older than yesterday are marked ``is_final`` and never re-queried.
- The rolling-24h headline is a separate ``now-24h .. now`` sum, recomputed every run and kept distinct
  from the calendar-day bins.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from django.db.models import Max

from humanityrules_app.models import App, AppDailyCost, CostRefreshJob
from humanityrules_app.services.cost import bedrock, cost_source
from humanityrules_app.services.infra_customer import bedrock_logging_utils

logger = logging.getLogger(__name__)

# The active cost sources. Adding a source = implement ``CostSource`` (cost_source.py) and append it.
COST_SOURCES: list[cost_source.CostSource] = [bedrock.BedrockCostSource()]


def run_refresh(job_id: str) -> None:
    """Execute one ``CostRefreshJob`` (the job-worker entry point): recompute and record the result."""
    job = CostRefreshJob.objects.select_related("app", "app__organization").get(id=job_id)
    try:
        summary = refresh_app(app=job.app)
        job.status = CostRefreshJob.Status.SUCCEEDED
        job.status_message = ""
        job.result = summary
    except Exception as exc:
        logger.exception("Cost refresh job %(id)s failed", {"id": job_id})
        job.status = CostRefreshJob.Status.FAILED
        job.status_message = str(exc)[:1000]
    job.save(update_fields=["status", "status_message", "result", "updated_at"])


def refresh_app(app: App) -> dict:
    """Recompute ``app``'s costs over the active window, freeze aged days, return a JSON-safe summary."""
    today = datetime.now(tz=timezone.utc).date()
    start_date = _recompute_start(app=app, today=today)
    end_date = today

    for source in COST_SOURCES:
        try:
            rows = source.collect(app=app, start_date=start_date, end_date=end_date)
        except Exception:
            logger.exception("Cost source %(key)s failed to collect for app %(app)s", {"key": source.key, "app": app.slug})
            continue
        _upsert_rows(app=app, source_key=source.key, rows=rows, start_date=start_date, end_date=end_date)

    _freeze_aged_days(app=app, today=today)
    return _rolling_24h_summary(app=app, start_date=start_date, end_date=end_date)


def _recompute_start(app: App, today: date) -> date:
    """First day to (re)query: from the day after the last frozen day, clamped to the retention floor."""
    retention_floor = today - timedelta(days=bedrock_logging_utils.LOG_GROUP_RETENTION_DAYS - 1)
    yesterday = today - timedelta(days=1)
    last_final_date = AppDailyCost.objects.filter(app=app, is_final=True).aggregate(
        latest=Max("date"),
    )["latest"]
    if last_final_date is None:
        start = retention_floor  # first run — backfill the whole retention window
    else:
        start = max(last_final_date + timedelta(days=1), retention_floor)
    # Always include today + yesterday, never start in the future.
    return min(start, yesterday)


def _upsert_rows(app: App, source_key: str, rows: list[cost_source.DailyCostRow], start_date: date, end_date: date) -> None:
    """Idempotently write each source row into ``AppDailyCost`` (unique on app/env/source/date/subkey)."""
    for row in rows:
        if row.date < start_date or row.date > end_date:
            continue  # defensive: ignore anything a source returned outside the window
        AppDailyCost.objects.update_or_create(
            app=app,
            environment_id=row.environment_id,
            source=source_key,
            date=row.date,
            subkey=row.subkey,
            defaults={
                "organization_id": app.organization_id,
                "cost_usd": row.cost_usd,
                "details": row.details,
                "is_final": False,
            },
        )


def _freeze_aged_days(app: App, today: date) -> None:
    """Mark every row older than yesterday final, so it is never re-queried."""
    AppDailyCost.objects.filter(app=app, date__lt=today - timedelta(days=1), is_final=False).update(
        is_final=True,
    )


def _rolling_24h_summary(app: App, start_date: date, end_date: date) -> dict:
    """Recompute the trailing-24h total per source and package a JSON-safe summary for the job result."""
    by_source: dict[str, Decimal] = {}
    for source in COST_SOURCES:
        try:
            by_source[source.key] = source.rolling_24h_usd(app=app)
        except Exception:
            logger.exception("Cost source %(key)s failed rolling-24h for app %(app)s", {"key": source.key, "app": app.slug})
            by_source[source.key] = Decimal(0)

    total = sum(by_source.values(), Decimal(0))
    return {
        "rolling_24h_usd": str(total),
        "rolling_24h_by_source": {key: str(value) for key, value in by_source.items()},
        "recomputed_from": start_date.isoformat(),
        "recomputed_to": end_date.isoformat(),
    }
