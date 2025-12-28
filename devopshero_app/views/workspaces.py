from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .base import get_app_shell_context


@login_required
def workspaces(request):
    context = get_app_shell_context(request=request, current_page="workspaces")
    
    if request.htmx:
        return render(request, "devopshero_app/workspaces.html", context=context)

    context["content_url"] = "/workspaces/"
    return render(request, "devopshero_app/app_shell.html", context=context)

