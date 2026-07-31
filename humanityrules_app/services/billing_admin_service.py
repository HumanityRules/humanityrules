"""Read-only platform billing snapshots and usage-event drill-downs."""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from django.core.paginator import Page, Paginator
from django.db.models import Count, Max, Min, OuterRef, Q, QuerySet, Subquery, Sum

from humanityrules_app import models


EVENTS_PER_PAGE = 50
LEDGER_ENTRIES_PER_PAGE = 50
AGED_PENDING_AFTER = datetime.timedelta(minutes=5)
_CLOSED_DAY_MUTATION_GRACE = datetime.timedelta(minutes=5)


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
    aged_pending_rating_count: int
    oldest_pending_received_at: datetime.datetime | None
    last_received_at: datetime.datetime | None
    credits_charged: Decimal
    negative_balance_organization_count: int
    integrity_issue_organization_count: int


@dataclass(frozen=True)
class BillingAppRow:
    app_id: UUID
    app_slug: str
    owner_username: str
    event_count: int
    pending_rating_count: int
    aged_pending_rating_count: int
    oldest_pending_received_at: datetime.datetime | None
    credits_charged: Decimal
    last_occurred_at: datetime.datetime | None
    is_removed: bool


@dataclass
class BillingOrganizationGroup:
    organization: models.Organization
    balance_credits: Decimal | None
    credits_charged: Decimal
    net_ledger_movement: Decimal
    ledger_entry_count: int
    event_count: int
    pending_rating_count: int
    aged_pending_rating_count: int
    oldest_pending_received_at: datetime.datetime | None
    last_activity_at: datetime.datetime | None
    integrity_issues: tuple[str, ...]
    apps: list[BillingAppRow] = field(default_factory=list)

    @property
    def has_integrity_issue(self) -> bool:
        """Return whether the balance or ledger violates an accounting invariant."""
        return bool(self.integrity_issues)


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
class BillingLedgerPage:
    page: Page[models.BillingLedgerEntry]


@dataclass(frozen=True)
class _UsageMetrics:
    event_count: int
    pending_rating_count: int
    aged_pending_rating_count: int
    oldest_pending_received_at: datetime.datetime | None
    last_occurred_at: datetime.datetime | None
    latest_app_slug: str
    latest_owner_username: str


@dataclass(frozen=True)
class _UsageIdentity:
    app_slug: str
    owner_username: str


@dataclass(frozen=True)
class _OrganizationUsageMetrics:
    event_count: int
    pending_rating_count: int
    aged_pending_rating_count: int
    oldest_pending_received_at: datetime.datetime | None
    last_occurred_at: datetime.datetime | None


@dataclass(frozen=True)
class _WindowLedgerMetrics:
    credits_charged: Decimal
    net_movement: Decimal
    entry_count: int


@dataclass(frozen=True)
class _LedgerAccountState:
    total: Decimal
    entry_count: int
    last_created_at: datetime.datetime | None


@dataclass(frozen=True)
class _PendingRatingMetrics:
    count: int
    aged_count: int
    oldest_received_at: datetime.datetime | None


class BillingAppNotFound(Exception):
    """The requested app is neither current nor represented in billing history."""


def resolve_billing_window(requested_key: str, now: datetime.datetime) -> BillingWindow:
    """Resolve a supported window key, falling back to the billing default."""
    choice = next((item for item in BILLING_WINDOW_CHOICES if item.key == requested_key), None)
    if choice is None:
        choice = next(item for item in BILLING_WINDOW_CHOICES if item.key == DEFAULT_BILLING_WINDOW_KEY)
    starts_at = now - choice.duration if choice.duration is not None else None
    return BillingWindow(key=choice.key, label=choice.label, starts_at=starts_at)


def build_billing_snapshot(window: BillingWindow, now: datetime.datetime) -> BillingSnapshot:
    """Build the cross-org billing overview from current apps and durable usage attribution."""
    event_scope = _event_scope(window=window)
    aged_pending_before = now - AGED_PENDING_AFTER
    aggregate = event_scope.aggregate(
        event_count=Count("id"),
        last_received_at=Max("created_at"),
    )
    pending_scope = models.BillingUsageEvent.objects.filter(rated_at__isnull=True)
    pending_aggregate = pending_scope.aggregate(
        pending_rating_count=Count("id"),
        aged_pending_rating_count=Count("id", filter=Q(created_at__lt=aged_pending_before)),
        oldest_pending_received_at=Min("created_at"),
    )
    pending_by_app = _pending_rating_metrics_by_app(
        pending_scope=pending_scope,
        aged_pending_before=aged_pending_before,
    )
    pending_by_organization = _pending_rating_metrics_by_organization(
        pending_scope=pending_scope,
        aged_pending_before=aged_pending_before,
    )
    usage_by_app = _usage_metrics_by_app(event_scope=event_scope, pending_by_app=pending_by_app)
    usage_by_organization = _usage_metrics_by_organization(
        event_scope=event_scope,
        pending_by_organization=pending_by_organization,
    )
    identities_by_app = _usage_identities_by_app()
    organizations = list(models.Organization.objects.order_by("slug"))
    window_ledger_by_organization, charged_credits_by_app = _window_ledger_metrics(window=window)
    account_state_by_organization = _ledger_account_states()
    balances_by_organization = {
        balance.organization_id: balance
        for balance in models.BillingBalance.objects.all()
    }
    integrity_by_organization = _integrity_issues_by_organization(
        organizations=organizations,
        account_state_by_organization=account_state_by_organization,
        balances_by_organization=balances_by_organization,
    )
    groups_by_organization = {
        organization.id: _organization_group(
            organization=organization,
            usage=usage_by_organization.get(organization.id),
            window_ledger=window_ledger_by_organization.get(organization.id),
            account_state=account_state_by_organization.get(organization.id),
            balance=balances_by_organization.get(organization.id),
            integrity_issues=integrity_by_organization[organization.id],
        )
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
                aged_pending_rating_count=metrics.aged_pending_rating_count if metrics is not None else 0,
                oldest_pending_received_at=metrics.oldest_pending_received_at if metrics is not None else None,
                credits_charged=charged_credits_by_app.get(key, Decimal(0)),
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
                aged_pending_rating_count=metrics.aged_pending_rating_count if metrics is not None else 0,
                oldest_pending_received_at=metrics.oldest_pending_received_at if metrics is not None else None,
                credits_charged=charged_credits_by_app.get(key, Decimal(0)),
                last_occurred_at=metrics.last_occurred_at if metrics is not None else None,
                is_removed=True,
            )
        )

    for group in groups_by_organization.values():
        group.apps.sort(key=lambda app: (app.is_removed, app.app_slug))

    summary = BillingSummary(
        event_count=aggregate["event_count"],
        reporting_organization_count=event_scope.values("organization_id").distinct().count(),
        pending_rating_count=pending_aggregate["pending_rating_count"],
        aged_pending_rating_count=pending_aggregate["aged_pending_rating_count"],
        oldest_pending_received_at=pending_aggregate["oldest_pending_received_at"],
        last_received_at=aggregate["last_received_at"],
        credits_charged=sum(
            (metrics.credits_charged for metrics in window_ledger_by_organization.values()),
            Decimal(0),
        ),
        negative_balance_organization_count=sum(
            1 for balance in balances_by_organization.values() if balance.credits < 0
        ),
        integrity_issue_organization_count=sum(1 for issues in integrity_by_organization.values() if issues),
    )
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


def build_billing_ledger_page(
    organization: models.Organization,
    window: BillingWindow,
    page_number: str | None,
) -> BillingLedgerPage:
    """Return one organization's paginated ledger entries by posting time."""
    ledger_scope = _ledger_scope(window=window).filter(organization=organization)
    paginator = Paginator(ledger_scope.order_by("-created_at", "-id"), LEDGER_ENTRIES_PER_PAGE)
    return BillingLedgerPage(page=paginator.get_page(page_number))


def _event_scope(window: BillingWindow) -> QuerySet[models.BillingUsageEvent]:
    """Apply the selected occurrence-time window to billing events."""
    event_scope = models.BillingUsageEvent.objects.all()
    if window.starts_at is not None:
        event_scope = event_scope.filter(occurred_at__gte=window.starts_at)
    return event_scope


def _ledger_scope(window: BillingWindow) -> QuerySet[models.BillingLedgerEntry]:
    """Apply the selected posting-time window to ledger entries."""
    ledger_scope = models.BillingLedgerEntry.objects.all()
    if window.starts_at is not None:
        ledger_scope = ledger_scope.filter(created_at__gte=window.starts_at)
    return ledger_scope


def _usage_metrics_by_app(
    event_scope: QuerySet[models.BillingUsageEvent],
    pending_by_app: dict[tuple[UUID, UUID], _PendingRatingMetrics],
) -> dict[tuple[UUID, UUID], _UsageMetrics]:
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
            last_occurred_at=Max("occurred_at"),
            latest_app_slug=Subquery(latest_event.values("app_slug")[:1]),
            latest_owner_username=Subquery(latest_event.values("owner_username")[:1]),
        )
        .order_by()
    )
    rows_by_app = {
        (row["organization_id"], row["app_id"]): row
        for row in rows
    }
    return {
        key: _UsageMetrics(
            event_count=rows_by_app[key]["event_count"] if key in rows_by_app else 0,
            pending_rating_count=pending_by_app[key].count if key in pending_by_app else 0,
            aged_pending_rating_count=pending_by_app[key].aged_count if key in pending_by_app else 0,
            oldest_pending_received_at=pending_by_app[key].oldest_received_at if key in pending_by_app else None,
            last_occurred_at=rows_by_app[key]["last_occurred_at"] if key in rows_by_app else None,
            latest_app_slug=rows_by_app[key]["latest_app_slug"] if key in rows_by_app else "",
            latest_owner_username=rows_by_app[key]["latest_owner_username"] if key in rows_by_app else "",
        )
        for key in rows_by_app.keys() | pending_by_app.keys()
    }


def _usage_metrics_by_organization(
    event_scope: QuerySet[models.BillingUsageEvent],
    pending_by_organization: dict[UUID, _PendingRatingMetrics],
) -> dict[UUID, _OrganizationUsageMetrics]:
    """Aggregate selected-window usage and rating health per organization."""
    rows = (
        event_scope
        .values("organization_id")
        .annotate(
            event_count=Count("id"),
            last_occurred_at=Max("occurred_at"),
        )
        .order_by()
    )
    rows_by_organization = {row["organization_id"]: row for row in rows}
    return {
        organization_id: _OrganizationUsageMetrics(
            event_count=rows_by_organization[organization_id]["event_count"] if organization_id in rows_by_organization else 0,
            pending_rating_count=pending_by_organization[organization_id].count if organization_id in pending_by_organization else 0,
            aged_pending_rating_count=(
                pending_by_organization[organization_id].aged_count if organization_id in pending_by_organization else 0
            ),
            oldest_pending_received_at=(
                pending_by_organization[organization_id].oldest_received_at
                if organization_id in pending_by_organization
                else None
            ),
            last_occurred_at=(
                rows_by_organization[organization_id]["last_occurred_at"]
                if organization_id in rows_by_organization
                else None
            ),
        )
        for organization_id in rows_by_organization.keys() | pending_by_organization.keys()
    }


def _pending_rating_metrics_by_app(
    pending_scope: QuerySet[models.BillingUsageEvent],
    aged_pending_before: datetime.datetime,
) -> dict[tuple[UUID, UUID], _PendingRatingMetrics]:
    """Return all-time pending-rating health per app."""
    rows = (
        pending_scope
        .values("organization_id", "app_id")
        .annotate(
            pending_rating_count=Count("id"),
            aged_pending_rating_count=Count("id", filter=Q(created_at__lt=aged_pending_before)),
            oldest_pending_received_at=Min("created_at"),
        )
        .order_by()
    )
    return {
        (row["organization_id"], row["app_id"]): _PendingRatingMetrics(
            count=row["pending_rating_count"],
            aged_count=row["aged_pending_rating_count"],
            oldest_received_at=row["oldest_pending_received_at"],
        )
        for row in rows
    }


def _pending_rating_metrics_by_organization(
    pending_scope: QuerySet[models.BillingUsageEvent],
    aged_pending_before: datetime.datetime,
) -> dict[UUID, _PendingRatingMetrics]:
    """Return all-time pending-rating health per organization."""
    rows = (
        pending_scope
        .values("organization_id")
        .annotate(
            pending_rating_count=Count("id"),
            aged_pending_rating_count=Count("id", filter=Q(created_at__lt=aged_pending_before)),
            oldest_pending_received_at=Min("created_at"),
        )
        .order_by()
    )
    return {
        row["organization_id"]: _PendingRatingMetrics(
            count=row["pending_rating_count"],
            aged_count=row["aged_pending_rating_count"],
            oldest_received_at=row["oldest_pending_received_at"],
        )
        for row in rows
    }


def _window_ledger_metrics(
    window: BillingWindow,
) -> tuple[dict[UUID, _WindowLedgerMetrics], dict[tuple[UUID, UUID], Decimal]]:
    """Aggregate selected-window ledger movements by organization and charge credits by app."""
    credits_by_organization: dict[UUID, Decimal] = {}
    movement_by_organization: dict[UUID, Decimal] = {}
    entry_count_by_organization: dict[UUID, int] = {}
    charged_credits_by_app: dict[tuple[UUID, UUID], Decimal] = {}

    entries = _ledger_scope(window=window).only("organization_id", "type", "amount", "metadata")
    for entry in entries:
        organization_id = entry.organization_id
        movement_by_organization[organization_id] = movement_by_organization.get(organization_id, Decimal(0)) + entry.amount
        entry_count_by_organization[organization_id] = entry_count_by_organization.get(organization_id, 0) + 1
        if entry.type != models.BillingLedgerEntry.Type.CHARGE:
            continue

        credits_charged = -entry.amount
        credits_by_organization[organization_id] = credits_by_organization.get(organization_id, Decimal(0)) + credits_charged
        app_id = UUID(entry.metadata["app_id"])
        app_key = (organization_id, app_id)
        charged_credits_by_app[app_key] = charged_credits_by_app.get(app_key, Decimal(0)) + credits_charged

    metrics_by_organization = {
        organization_id: _WindowLedgerMetrics(
            credits_charged=credits_by_organization.get(organization_id, Decimal(0)),
            net_movement=movement_by_organization[organization_id],
            entry_count=entry_count_by_organization[organization_id],
        )
        for organization_id in movement_by_organization
    }
    return metrics_by_organization, charged_credits_by_app


def _ledger_account_states() -> dict[UUID, _LedgerAccountState]:
    """Return each organization's all-time ledger projection inputs."""
    rows = (
        models.BillingLedgerEntry.objects
        .values("organization_id")
        .annotate(total=Sum("amount"), entry_count=Count("id"), last_created_at=Max("created_at"))
        .order_by()
    )
    return {
        row["organization_id"]: _LedgerAccountState(
            total=row["total"],
            entry_count=row["entry_count"],
            last_created_at=row["last_created_at"],
        )
        for row in rows
    }


def _integrity_issues_by_organization(
    organizations: list[models.Organization],
    account_state_by_organization: dict[UUID, _LedgerAccountState],
    balances_by_organization: dict[UUID, models.BillingBalance],
) -> dict[UUID, tuple[str, ...]]:
    """Check balance projections and closed-day charge immutability for the dashboard."""
    issues_by_organization: dict[UUID, list[str]] = {organization.id: [] for organization in organizations}
    for organization in organizations:
        account_state = account_state_by_organization.get(
            organization.id,
            _LedgerAccountState(total=Decimal(0), entry_count=0, last_created_at=None),
        )
        balance = balances_by_organization.get(organization.id)
        if balance is None and account_state.entry_count:
            issues_by_organization[organization.id].append("Ledger activity has no balance row")
        elif balance is not None and balance.credits != account_state.total:
            drift = balance.credits - account_state.total
            credit_unit = "credit" if abs(drift) == 1 else "credits"
            issues_by_organization[organization.id].append(
                f"Balance differs from the ledger by {drift} {credit_unit}",
            )

    day_charges = models.BillingLedgerEntry.objects.filter(
        type=models.BillingLedgerEntry.Type.CHARGE,
        idempotency_key__startswith="charge:",
    ).only("organization_id", "idempotency_key", "updated_at")
    for entry in day_charges:
        try:
            posting_date = datetime.date.fromisoformat(entry.idempotency_key.rsplit(":", 1)[-1])
        except ValueError:
            issues_by_organization[entry.organization_id].append("Charge entry has an invalid posting date")
            continue
        day_close = datetime.datetime.combine(
            posting_date + datetime.timedelta(days=1),
            datetime.time.min,
            tzinfo=datetime.UTC,
        )
        if entry.updated_at > day_close + _CLOSED_DAY_MUTATION_GRACE:
            issues_by_organization[entry.organization_id].append("A closed-day charge was modified after posting")

    return {
        organization_id: tuple(issues)
        for organization_id, issues in issues_by_organization.items()
    }


def _organization_group(
    organization: models.Organization,
    usage: _OrganizationUsageMetrics | None,
    window_ledger: _WindowLedgerMetrics | None,
    account_state: _LedgerAccountState | None,
    balance: models.BillingBalance | None,
    integrity_issues: tuple[str, ...],
) -> BillingOrganizationGroup:
    """Combine one organization's current account state with selected-window activity."""
    last_usage_at = usage.last_occurred_at if usage is not None else None
    last_ledger_at = account_state.last_created_at if account_state is not None else None
    last_activity_at = max(
        (timestamp for timestamp in (last_usage_at, last_ledger_at) if timestamp is not None),
        default=None,
    )
    return BillingOrganizationGroup(
        organization=organization,
        balance_credits=balance.credits if balance is not None else None,
        credits_charged=window_ledger.credits_charged if window_ledger is not None else Decimal(0),
        net_ledger_movement=window_ledger.net_movement if window_ledger is not None else Decimal(0),
        ledger_entry_count=window_ledger.entry_count if window_ledger is not None else 0,
        event_count=usage.event_count if usage is not None else 0,
        pending_rating_count=usage.pending_rating_count if usage is not None else 0,
        aged_pending_rating_count=usage.aged_pending_rating_count if usage is not None else 0,
        oldest_pending_received_at=usage.oldest_pending_received_at if usage is not None else None,
        last_activity_at=last_activity_at,
        integrity_issues=integrity_issues,
    )


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
    if metrics is not None and metrics.latest_owner_username:
        return metrics.latest_owner_username
    if identity is not None:
        return identity.owner_username
    if app.created_by is not None:
        return app.created_by.username
    return ""
