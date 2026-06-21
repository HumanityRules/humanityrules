from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from ..models import Organization, OrganizationInvite, OrganizationMembership
from . import base


@login_required
def settings(request: HttpRequest) -> HttpResponse:
    context = base.get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "personal"

    if request.htmx:
        return render(request, "humanityrules_app/settings/personal.html", context=context)

    context["content_url"] = "/settings/"
    return render(request, "humanityrules_app/app_shell.html", context=context)


@login_required
def settings_personal(request: HttpRequest) -> HttpResponse:
    context = base.get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "personal"

    if request.htmx:
        return render(request, "humanityrules_app/settings/personal.html", context=context)

    context["content_url"] = "/settings/personal/"
    return render(request, "humanityrules_app/app_shell.html", context=context)


@login_required
def settings_organization(request: HttpRequest) -> HttpResponse:
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="settings")
        context["content_url"] = "/settings/organization/"
        return render(request, "humanityrules_app/app_shell.html", context=context)

    org = request.user.current_organization

    members = (
        OrganizationMembership.objects
        .filter(organization=org)
        .select_related("user")
        .order_by("-created_at")
    )
    # Invites are WorkOS-only for now: an OIDC org's invitee would land in
    # /auth/login/ (WorkOS) and end up with a workos_user_id that the org's
    # Okta-configured policy proxy will reject. OIDC invites need a separate
    # path through /oidc/login/?org=<slug> + oidc_callback (TODO).
    invites_enabled = org.auth_provider == Organization.AuthProvider.WORKOS
    pending_invites = (
        OrganizationInvite.objects
        .filter(organization=org, accepted_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now())
        .select_related("invited_by")
        .order_by("-created_at")
    ) if invites_enabled else []
    invite_base_url = request.build_absolute_uri("/invite/")

    context = base.get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "organization"
    context["org"] = org
    context["members"] = members
    context["invites_enabled"] = invites_enabled
    context["pending_invites"] = pending_invites
    context["invite_base_url"] = invite_base_url
    return render(request, "humanityrules_app/settings/organization.html", context=context)


@login_required
def settings_billing(request: HttpRequest) -> HttpResponse:
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    context = base.get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "billing"

    if request.htmx:
        return render(request, "humanityrules_app/settings/billing.html", context=context)

    context["content_url"] = "/settings/billing/"
    return render(request, "humanityrules_app/app_shell.html", context=context)

