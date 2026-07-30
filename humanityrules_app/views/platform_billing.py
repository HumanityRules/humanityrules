"""Staff-only platform billing overview and usage-event inspection."""

from uuid import UUID

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from humanityrules_app import models
from humanityrules_app.services import billing_admin_service
from humanityrules_app.views import platform_access


@platform_access.platform_staff_required
@require_GET
def platform_billing(request: HttpRequest) -> HttpResponse:
    """Render the cross-organization billing operations snapshot."""
    window = billing_admin_service.resolve_billing_window(
        requested_key=request.GET.get("window", billing_admin_service.DEFAULT_BILLING_WINDOW_KEY),
        now=timezone.now(),
    )
    snapshot = billing_admin_service.build_billing_snapshot(window=window)
    context = {
        "billing_window": window,
        "billing_window_choices": billing_admin_service.BILLING_WINDOW_CHOICES,
        "snapshot": snapshot,
    }
    return render(request, "humanityrules_app/platform_billing/platform_billing.html", context=context)


@platform_access.platform_staff_required
@require_GET
def platform_billing_app_events(request: HttpRequest, organization_id: UUID, app_id: UUID) -> HttpResponse:
    """Render one org-scoped app's paginated raw billing events."""
    organization = get_object_or_404(models.Organization, id=organization_id)
    window = billing_admin_service.resolve_billing_window(
        requested_key=request.GET.get("window", billing_admin_service.DEFAULT_BILLING_WINDOW_KEY),
        now=timezone.now(),
    )
    try:
        event_page = billing_admin_service.build_billing_event_page(
            organization=organization,
            app_id=app_id,
            window=window,
            page_number=request.GET.get("page"),
        )
    except billing_admin_service.BillingAppNotFound as exc:
        raise Http404 from exc
    context = {
        "app_id": app_id,
        "billing_window": window,
        "event_page": event_page,
        "organization": organization,
    }
    return render(request, "humanityrules_app/platform_billing/_billing_app_events.html", context=context)
