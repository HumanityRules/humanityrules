from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ..models import App, Datastore
from .base import get_app_shell_context


@login_required
def dashboard(request):
    context = get_app_shell_context(request=request, current_page="dashboard")
    org = request.user.current_organization
    
    apps = App.objects.filter(organization=org).select_related("workspace", "repository").order_by("-created_at")
    datastores = Datastore.objects.filter(workspace__organization=org).select_related("workspace").order_by("-created_at")
    
    context["apps"] = apps
    context["datastores"] = datastores
    
    if request.htmx:        
        return render(request, "devopshero_app/dashboard.html", context=context)

    context["content_url"] = "/dashboard/"
    return render(request, "devopshero_app/app_shell.html", context=context)

