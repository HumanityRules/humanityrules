from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from . import base


@login_required
def settings(request: HttpRequest) -> HttpResponse:
    context = base.get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "personal"

    if request.htmx:
        return render(request, "devopshero_app/settings/personal.html", context=context)

    context["content_url"] = "/settings/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def settings_personal(request: HttpRequest) -> HttpResponse:
    context = base.get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "personal"

    if request.htmx:
        return render(request, "devopshero_app/settings/personal.html", context=context)

    context["content_url"] = "/settings/personal/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def settings_organization(request: HttpRequest) -> HttpResponse:
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="settings")
        context["content_url"] = "/settings/organization/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    org = request.user.current_organization

    context = base.get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "organization"
    context["org"] = org
    return render(request, "devopshero_app/settings/organization.html", context=context)


@login_required
def settings_billing(request: HttpRequest) -> HttpResponse:
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    context = base.get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "billing"

    if request.htmx:
        return render(request, "devopshero_app/settings/billing.html", context=context)

    context["content_url"] = "/settings/billing/"
    return render(request, "devopshero_app/app_shell.html", context=context)

