"""Read-only platform billing snapshots and usage-event drill-downs."""

import datetime
from dataclasses import dataclass, field
from uuid import UUID

from django.core.paginator import Page, Paginator
from django.db.models import Count, Max, OuterRef, Q, QuerySet, Subquery

from humanityrules_app import models


EVENTS_PER_PAGE = 50


@dataclass(frozen=True)
class BillingWindowChoice:
    key: str
    label: str
    duration: datetime.timedelta | None


BILLING_WINDOW_CHOICES = (
    BillingWindowChoice(key="24h", label="24h", duration=datetime.timedelta(hours=24)),
    BillingWindowChoice(key="7d", label="7d", duration=datetime.timedelta(days=7)),
    BillingWindowChoice(key="30d", label="30d", duration=datetime.timedelta(days=30)),
    BillingWindowChoice(key="all", label="All", duration=None),
)
DEFAULT_BILLING_WINDOW_KEY = "30d"


@dataclass(frozen=True)
class BillingWindow:
    key: str
    label: str
    starts_at: datetime.datetime | None


@dataclass(frozen=True)
class BillingSummary:
    event_count: int
    reporting_organization_count: int
    pending_rating_count: int
    last_received_at: datetime.datetime | None


@dataclass(frozen=True)
class BillingAppRow:
    app_id: UUID
    app_slug: str
    owner_username: str
    event_count: int
    pending_rating_count: int
    last_occurred_at: datetime.datetime | None
    is_removed: bool


@dataclass
class BillingOrganizationGroup:
    organization: models.Organization
    apps: list[BillingAppRow] = field(default_factory=list)


@dataclass(frozen=True)
class BillingSnapshot:
    summary: BillingSummary
    organization_groups: list[BillingOrganizationGroup]


@dataclass(frozen=True)
class BillingEventPage:
    app_slug: str
    owner_username: str
    is_removed: bool
    page: Page[models.BillingUsageEvent]


@dataclass(frozen=True)
class _UsageMetrics:
    event_count: int
    pending_rating_count: int
    last_occurred_at: datetime.datetime | None
    latest_app_slug: str
    latest_owner_username: str


@dataclass(frozen=True)
class _UsageIdentity:
    app_slug: str
    owner_username: str


class BillingAppNotFound(Exception):
    """The requested app is neither current nor represented in billing history."""


def resolve_billing_window(requested_key: str, now: datetime.datetime) -> BillingWindow:
    """Resolve a supported window key, falling back to the billing default."""
    choice = next((item for item in BILLING_WINDOW_CHOICES if item.key == requested_key), None)
    if choice is None:
        choice = next(item for item in BILLING_WINDOW_CHOICES if item.key == DEFAULT_BILLING_WINDOW_KEY)
    starts_at = now - choice.duration if choice.duration is not None else None
    return BillingWindow(key=choice.key, label=choice.label, starts_at=starts_at)


def build_billing_snapshot(window: BillingWindow) -> BillingSnapshot:
    """Build the cross-org billing overview from current apps and durable usage attribution."""
    event_scope = _event_scope(window=window)
    aggregate = event_scope.aggregate(
        event_count=Count("id"),
        pending_rating_count=Count("id", filter=Q(rated_at__isnull=True)),
        last_received_at=Max("created_at"),
    )
    summary = BillingSummary(
        event_count=aggregate["event_count"],
        reporting_organization_count=event_scope.values("organization_id").distinct().count(),
        pending_rating_count=aggregate["pending_rating_count"],
        last_received_at=aggregate["last_received_at"],
    )

    usage_by_app = _usage_metrics_by_app(event_scope=event_scope)
    identities_by_app = _usage_identities_by_app()
    organizations = list(models.Organization.objects.order_by("slug"))
    groups_by_organization = {
        organization.id: BillingOrganizationGroup(organization=organization)
        for organization in organizations
    }

    current_app_keys: set[tuple[UUID, UUID]] = set()
    current_apps = models.App.objects.select_related("organization", "created_by").order_by("organization__slug", "slug")
    for app in current_apps:
        key = (app.organization_id, app.id)
        current_app_keys.add(key)
        metrics = usage_by_app.get(key)
        identity = identities_by_app.get(key)
        owner_username = _owner_username_for_current_app(app=app, metrics=metrics, identity=identity)
        groups_by_organization[app.organization_id].apps.append(
            BillingAppRow(
                app_id=app.id,
                app_slug=app.slug,
                owner_username=owner_username,
                event_count=metrics.event_count if metrics is not None else 0,
                pending_rating_count=metrics.pending_rating_count if metrics is not None else 0,
                last_occurred_at=metrics.last_occurred_at if metrics is not None else None,
                is_removed=False,
            )
        )

    for key, identity in identities_by_app.items():
        if key in current_app_keys:
            continue
        organization_id, app_id = key
        metrics = usage_by_app.get(key)
        groups_by_organization[organization_id].apps.append(
            BillingAppRow(
                app_id=app_id,
                app_slug=identity.app_slug,
                owner_username=identity.owner_username,
                event_count=metrics.event_count if metrics is not None else 0,
                pending_rating_count=metrics.pending_rating_count if metrics is not None else 0,
                last_occurred_at=metrics.last_occurred_at if metrics is not None else None,
                is_removed=True,
            )
        )

    for group in groups_by_organization.values():
        group.apps.sort(key=lambda app: (app.is_removed, app.app_slug))

    return BillingSnapshot(summary=summary, organization_groups=list(groups_by_organization.values()))


def build_billing_event_page(
    organization: models.Organization,
    app_id: UUID,
    window: BillingWindow,
    page_number: str | None,
) -> BillingEventPage:
    """Return one org-scoped app's paginated raw usage events."""
    current_app = (
        models.App.objects
        .filter(organization=organization, id=app_id)
        .select_related("created_by")
        .first()
    )
    all_events = models.BillingUsageEvent.objects.filter(organization=organization, app_id=app_id)
    latest_event = all_events.order_by("-occurred_at", "-created_at").first()
    if current_app is None and latest_event is None:
        raise BillingAppNotFound

    app_slug = current_app.slug if current_app is not None else latest_event.app_slug
    if latest_event is not None:
        owner_username = latest_event.owner_username
    elif current_app is not None and current_app.created_by is not None:
        owner_username = current_app.created_by.username
    else:
        owner_username = ""

    event_scope = all_events
    if window.starts_at is not None:
        event_scope = event_scope.filter(occurred_at__gte=window.starts_at)
    paginator = Paginator(event_scope.order_by("-occurred_at", "-created_at"), EVENTS_PER_PAGE)
    return BillingEventPage(
        app_slug=app_slug,
        owner_username=owner_username,
        is_removed=current_app is None,
        page=paginator.get_page(page_number),
    )


def _event_scope(window: BillingWindow) -> QuerySet[models.BillingUsageEvent]:
    """Apply the selected occurrence-time window to billing events."""
    event_scope = models.BillingUsageEvent.objects.all()
    if window.starts_at is not None:
        event_scope = event_scope.filter(occurred_at__gte=window.starts_at)
    return event_scope


def _usage_metrics_by_app(event_scope: QuerySet[models.BillingUsageEvent]) -> dict[tuple[UUID, UUID], _UsageMetrics]:
    """Aggregate relational billing facts per app without aggregating quantities JSON."""
    latest_event = event_scope.filter(
        organization_id=OuterRef("organization_id"),
        app_id=OuterRef("app_id"),
    ).order_by("-occurred_at", "-created_at")
    rows = (
        event_scope
        .values("organization_id", "app_id")
        .annotate(
            event_count=Count("id"),
            pending_rating_count=Count("id", filter=Q(rated_at__isnull=True)),
            last_occurred_at=Max("occurred_at"),
            latest_app_slug=Subquery(latest_event.values("app_slug")[:1]),
            latest_owner_username=Subquery(latest_event.values("owner_username")[:1]),
        )
        .order_by()
    )
    return {
        (row["organization_id"], row["app_id"]): _UsageMetrics(
            event_count=row["event_count"],
            pending_rating_count=row["pending_rating_count"],
            last_occurred_at=row["last_occurred_at"],
            latest_app_slug=row["latest_app_slug"],
            latest_owner_username=row["latest_owner_username"],
        )
        for row in rows
    }


def _usage_identities_by_app() -> dict[tuple[UUID, UUID], _UsageIdentity]:
    """Retain the latest snapshot identity for every app ever represented in billing."""
    all_events = models.BillingUsageEvent.objects.all()
    latest_event = all_events.filter(
        organization_id=OuterRef("organization_id"),
        app_id=OuterRef("app_id"),
    ).order_by("-occurred_at", "-created_at")
    rows = (
        all_events
        .values("organization_id", "app_id")
        .annotate(
            latest_app_slug=Subquery(latest_event.values("app_slug")[:1]),
            latest_owner_username=Subquery(latest_event.values("owner_username")[:1]),
        )
        .order_by()
    )
    return {
        (row["organization_id"], row["app_id"]): _UsageIdentity(
            app_slug=row["latest_app_slug"],
            owner_username=row["latest_owner_username"],
        )
        for row in rows
    }


def _owner_username_for_current_app(app: models.App, metrics: _UsageMetrics | None, identity: _UsageIdentity | None) -> str:
    """Prefer reported attribution, falling back to the app creator."""
    if metrics is not None:
        return metrics.latest_owner_username
    if identity is not None:
        return identity.owner_username
    if app.created_by is not None:
        return app.created_by.username
    return ""
