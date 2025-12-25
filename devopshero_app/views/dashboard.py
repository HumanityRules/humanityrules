from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .base import get_app_shell_context


@login_required
def dashboard(request):
    context = get_app_shell_context(current_page="dashboard")
    
    if request.htmx:        
        return render(request, "devopshero_app/dashboard.html", context=context)

    context["content_url"] = "/dashboard/"
    return render(request, "devopshero_app/app_shell.html", context=context)

