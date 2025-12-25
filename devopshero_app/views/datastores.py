from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .base import get_app_shell_context


@login_required
def datastores(request):
    context = get_app_shell_context(current_page="datastores")
    
    if request.htmx:
        return render(request, "devopshero_app/datastores.html", context=context)

    context["content_url"] = "/datastores/"
    return render(request, "devopshero_app/app_shell.html", context=context)

