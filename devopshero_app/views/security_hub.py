import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from .. import models
from ..services import abac_service
from . import base

logger = logging.getLogger(__name__)


@login_required
def security_hub(request: HttpRequest) -> HttpResponse:
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    organization = request.user.current_organization
    all_requests = (
        models.AppPermissionRequest.objects.filter(app__organization=organization)
        .select_related("app", "environment", "created_by")
        .order_by("-created_at")
    )
    visible_requests = abac_service.filter_visible_app_permission_requests(
        organization=organization,
        user=request.user,
        queryset=all_requests,
    )

    context = base.get_app_shell_context(request=request, current_page="security")
    context["active_tab"] = "hub"
    context["permission_issue_rows"] = []
    context["app_permission_request_rows"] = list(visible_requests[:20])

    return render(request=request, template_name="devopshero_app/security/security_hub.html", context=context)
