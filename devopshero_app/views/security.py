from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .base import get_app_shell_context


@login_required
def security(request):
    context = get_app_shell_context(current_page="security")
    
    if request.htmx:
        return render(request, "devopshero_app/security.html", context=context)

    context["content_url"] = "/security/"
    return render(request, "devopshero_app/app_shell.html", context=context)

