from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .base import get_app_shell_context


@login_required
def apps(request):
    context = get_app_shell_context(request=request, current_page="apps")
    
    if request.htmx:
        return render(request, "devopshero_app/apps.html", context=context)

    context["content_url"] = "/apps/"
    return render(request, "devopshero_app/app_shell.html", context=context)

