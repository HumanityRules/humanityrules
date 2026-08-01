"""Render personal and organization settings inside the HTMX application shell.

Organization administrators also use this module to inspect billing and begin
hosted Stripe Checkout or customer-portal sessions. Stripe objects stay behind
the billing lifecycle service; these views only redirect to plain hosted URLs.
"""

import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from humanityrules_app.services.billing import billing_page, stripe_lifecycle

from ..models import Organization, OrganizationInvite, OrganizationMembership
from . import base

logger = logging.getLogger(__name__)


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
    forbidden = base.require_org_admin(request=request)
    if forbidden:
        return forbidden

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="settings")
        context["content_url"] = "/settings/billing/"
        return render(request=request, template_name="humanityrules_app/app_shell.html", context=context)

    organization = request.user.current_organization
    context = base.get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "billing"
    context["billing"] = billing_page.billing_page_context(organization=organization)
    return render(request=request, template_name="humanityrules_app/settings/billing.html", context=context)


@login_required
@csrf_protect
@require_POST
def settings_billing_checkout(request: HttpRequest) -> HttpResponse:
    """Redirect an organization administrator to hosted Operator Checkout."""
    forbidden = base.require_org_admin(request=request)
    if forbidden:
        return forbidden

    organization = request.user.current_organization
    billing = billing_page.billing_page_context(organization=organization)
    billing_path = "/settings/billing/"
    if not billing.stripe_configured or not billing.show_upgrade:
        logger.error(f"cannot start billing checkout for organization {organization.id}: checkout is unavailable")
        return HttpResponseRedirect(redirect_to=billing_path, status=303)

    return_url = request.build_absolute_uri(location=billing_path)
    checkout_url = stripe_lifecycle.create_operator_checkout_url(
        organization=organization,
        success_url=return_url,
        cancel_url=return_url,
    )
    return HttpResponseRedirect(redirect_to=checkout_url, status=303)


@login_required
@csrf_protect
@require_POST
def settings_billing_portal(request: HttpRequest) -> HttpResponse:
    """Redirect an organization administrator to Stripe's customer portal."""
    forbidden = base.require_org_admin(request=request)
    if forbidden:
        return forbidden

    organization = request.user.current_organization
    billing = billing_page.billing_page_context(organization=organization)
    billing_path = "/settings/billing/"
    if not billing.stripe_configured or not billing.show_manage_billing:
        logger.error(f"cannot start billing portal for organization {organization.id}: portal is unavailable")
        return HttpResponseRedirect(redirect_to=billing_path, status=303)

    return_url = request.build_absolute_uri(location=billing_path)
    portal_url = stripe_lifecycle.create_portal_url(
        organization=organization,
        return_url=return_url,
    )
    return HttpResponseRedirect(redirect_to=portal_url, status=303)
