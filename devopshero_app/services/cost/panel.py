"""Cache-first panel context for the app-detail cost widget.

Reads ``AppDailyCost`` (instant), enqueues a deduped recompute, and builds ready-to-render inline-SVG
bar geometry so the template carries no chart math. No AWS calls happen here — those run in the worker.
"""

import logging
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from django.db import transaction

from devopshero_app.models import App, AppDailyCost, CostRefreshJob

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30  # matches bedrock_logging_utils.LOG_GROUP_RETENTION_DAYS; widen if retention grows

# Bar/plot geometry in SVG user units. The chart renders at a fixed viewBox width and is scaled to
# 100% of its container by CSS (width:100%, height:auto), so it always fills the horizontal space and
# the bars spread evenly across it regardless of the panel width.
_VIEW_WIDTH = 1000
_AXIS_LEFT = 46  # left margin reserved for y-axis tick labels
_PAD_RIGHT = 14
_PLOT_TOP = 10
_PLOT_HEIGHT = 140
_AXIS_LABEL_HEIGHT = 18  # bottom strip for x-axis date labels
_BAR_FILL = 0.72  # fraction of each day's slot occupied by the bar; the remainder is the gap
_MIN_VISIBLE_SEGMENT = 1.0  # sliver so tiny non-zero costs stay visible
_Y_TICK_TARGET = 4  # approximate number of horizontal gridlines / y-axis ticks

# Distinct, mid-tone colors readable on both light and dark backgrounds; assigned per model.
_PALETTE = [
    "#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444",
    "#8b5cf6", "#14b8a6", "#f97316", "#ec4899", "#84cc16",
]


def enqueue_refresh(app: App) -> None:
    """Ensure exactly one active ``CostRefreshJob`` exists for ``app`` (collapses rapid reloads)."""
    with transaction.atomic():
        already_active = (
            CostRefreshJob.objects.select_for_update(skip_locked=True)
            .filter(app=app, status__in=CostRefreshJob.ACTIVE_STATUSES)
            .exists()
        )
        if already_active:
            return
        CostRefreshJob.objects.create(
            app=app,
            organization_id=app.organization_id,
            status=CostRefreshJob.Status.PENDING,
        )


def build_panel_context(app: App) -> dict:
    """Build the cost-chart fragment context from the cached table (read-only; the view enqueues)."""
    today = datetime.now(tz=timezone.utc).date()
    window_start = today - timedelta(days=WINDOW_DAYS - 1)
    day_axis = [window_start + timedelta(days=offset) for offset in range(WINDOW_DAYS)]

    cost_by_day_model, totals_by_model, has_estimate, last_updated = _read_costs(app=app, window_start=window_start)
    max_day_total = _max_day_total(cost_by_day_model=cost_by_day_model, day_axis=day_axis)
    model_order = [model for model, _ in sorted(totals_by_model.items(), key=lambda item: item[1], reverse=True)]
    colors = {model: _PALETTE[index % len(_PALETTE)] for index, model in enumerate(model_order)}

    return {
        "app": app,
        "is_refreshing": _is_refreshing(app=app),
        "rolling_24h_display": _rolling_24h_display(app=app),
        "window_total_display": _format_usd(sum(totals_by_model.values(), Decimal(0))),
        "window_days": WINDOW_DAYS,
        "chart": _build_chart(
            day_axis=day_axis, cost_by_day_model=cost_by_day_model, model_order=model_order,
            colors=colors, max_day_total=max_day_total,
        ),
        "legend": [
            {"label": _model_label(model), "color": colors[model], "total_display": _format_usd(totals_by_model[model])}
            for model in model_order
        ],
        "has_cost": bool(model_order),
        "has_estimate": has_estimate,
        "last_updated": last_updated,
    }


def _read_costs(
    app: App, window_start: date,
) -> tuple[dict[date, dict[str, Decimal]], dict[str, Decimal], bool, datetime | None]:
    """Aggregate cached rows per (day, model) and per model; report estimate flag and freshness."""
    cost_by_day_model: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    totals_by_model: dict[str, Decimal] = defaultdict(Decimal)
    has_estimate = False
    last_updated: datetime | None = None

    rows = AppDailyCost.objects.filter(app=app, date__gte=window_start).exclude(subkey="")
    for row in rows:
        cost_by_day_model[row.date][row.subkey] += row.cost_usd
        totals_by_model[row.subkey] += row.cost_usd
        if row.details.get("estimate") or not row.details.get("priced", True):
            has_estimate = True
        if last_updated is None or row.updated_at > last_updated:
            last_updated = row.updated_at

    return cost_by_day_model, totals_by_model, has_estimate, last_updated


def _max_day_total(cost_by_day_model: dict[date, dict[str, Decimal]], day_axis: list[date]) -> Decimal:
    """Largest single-day total across the visible window (chart's y-scale); 0 if no usage."""
    totals = [sum(cost_by_day_model[day].values(), Decimal(0)) for day in day_axis]
    return max(totals, default=Decimal(0))


def _build_chart(
    day_axis: list[date], cost_by_day_model: dict[date, dict[str, Decimal]], model_order: list[str],
    colors: dict[str, str], max_day_total: Decimal,
) -> dict:
    """Compute the fixed-viewBox SVG geometry, y-axis ticks, and per-bar stacked segments."""
    baseline_y = _PLOT_TOP + _PLOT_HEIGHT
    height = baseline_y + _AXIS_LABEL_HEIGHT
    plot_left = _AXIS_LEFT
    plot_right = _VIEW_WIDTH - _PAD_RIGHT
    slot = (plot_right - plot_left) / len(day_axis)
    bar_width = round(slot * _BAR_FILL, 2)

    # Bars scale to the top y-axis tick (>= the busiest day), so they sit under the top gridline.
    tick_values = _nice_axis_ticks(max_value=float(max_day_total), target_count=_Y_TICK_TARGET)
    axis_max = Decimal(str(tick_values[-1]))

    bars = []
    for index, day in enumerate(day_axis):
        slot_x = plot_left + index * slot
        bar_x = round(slot_x + (slot - bar_width) / 2, 2)
        day_total = sum(cost_by_day_model[day].values(), Decimal(0))
        segments = []
        cumulative = 0.0
        for model in model_order:
            cost = cost_by_day_model[day].get(model, Decimal(0))
            if cost <= 0:
                continue
            segment_height = _segment_height(cost=cost, axis_max=axis_max)
            cumulative += segment_height
            segments.append({
                "x": bar_x,
                "y": round(baseline_y - cumulative, 2),
                "width": bar_width,
                "height": round(segment_height, 2),
                "color": colors[model],
                "label": _model_label(model),
                "cost_display": _format_usd(cost),
            })
        show_label = index % 7 == 0 or index == len(day_axis) - 1
        bars.append({
            "segments": segments,
            "label_x": round(slot_x + slot / 2, 2),
            "show_label": show_label,
            "label": f"{day.strftime('%b')} {day.day}",
            "title": f"{day.strftime('%b')} {day.day} · {_format_usd(day_total)}",
        })

    return {
        "width": _VIEW_WIDTH,
        "height": height,
        "baseline_y": baseline_y,
        "plot_top": _PLOT_TOP,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "y_label_x": plot_left - 6,
        "y_ticks": _build_y_ticks(tick_values=tick_values, axis_max=axis_max, baseline_y=baseline_y),
        "bars": bars,
    }


def _build_y_ticks(tick_values: list[float], axis_max: Decimal, baseline_y: float) -> list[dict]:
    """Position each y-axis tick (gridline + right-aligned label); the 0 tick rides the baseline."""
    ticks = []
    for value in tick_values:
        value_decimal = Decimal(str(value))
        tick_y = baseline_y if axis_max <= 0 else round(baseline_y - float(value_decimal / axis_max) * _PLOT_HEIGHT, 2)
        ticks.append({
            "y": tick_y,
            "label_y": round(tick_y + 3, 2),  # nudge down so the label visually centers on the line
            "label": "$0" if value == 0 else _format_usd(value_decimal),
            "gridline": value > 0,  # the 0 line is already drawn as the baseline
        })
    return ticks


def _nice_axis_ticks(max_value: float, target_count: int) -> list[float]:
    """Ascending 'nice' tick values from 0 up to >= ``max_value`` (~``target_count`` ticks, 1/2/2.5/5 steps)."""
    if max_value <= 0:
        return [0.0]
    raw_step = max_value / target_count
    magnitude = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / magnitude
    if residual <= 1:
        nice = 1.0
    elif residual <= 2:
        nice = 2.0
    elif residual <= 2.5:
        nice = 2.5
    elif residual <= 5:
        nice = 5.0
    else:
        nice = 10.0
    step = nice * magnitude
    ticks = []
    value = 0.0
    while value < max_value - step * 1e-6 and len(ticks) < 12:
        ticks.append(round(value, 10))
        value += step
    ticks.append(round(value, 10))  # final tick is always >= max_value, so bars never overshoot the plot
    return ticks


def _segment_height(cost: Decimal, axis_max: Decimal) -> float:
    """Pixel height for one stacked segment (scaled to the top y-axis tick), with a 1px visibility floor."""
    if axis_max <= 0:
        return 0.0
    raw = float(cost / axis_max) * _PLOT_HEIGHT
    return max(raw, _MIN_VISIBLE_SEGMENT)


def _is_refreshing(app: App) -> bool:
    """True while a recompute is queued or running (drives the self-terminating HTMX poll)."""
    return CostRefreshJob.objects.filter(
        app=app, status__in=CostRefreshJob.ACTIVE_STATUSES,
    ).exists()


def _rolling_24h_display(app: App) -> str | None:
    """Trailing-24h total from the most recent succeeded job, or ``None`` if none has completed."""
    job = (
        CostRefreshJob.objects.filter(app=app, status=CostRefreshJob.Status.SUCCEEDED)
        .order_by("-created_at")
        .first()
    )
    if job is None:
        return None
    raw = job.result.get("rolling_24h_usd")
    if raw is None:
        return None
    try:
        return _format_usd(Decimal(str(raw)))
    except (ValueError, ArithmeticError):
        return None


def _model_label(model_key: str) -> str:
    """Human-readable model name from the normalized key (drop the provider prefix)."""
    return model_key.removeprefix("anthropic.")


def _format_usd(amount: Decimal) -> str:
    """Format a USD amount: 4 decimals under $1 (sub-cent costs are common), else 2 with separators."""
    if amount < 1:
        return f"${amount:.4f}"
    return f"${amount:,.2f}"
