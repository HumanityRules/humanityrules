from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ..models import AWSAccount, Organization
from .base import get_app_shell_context


@login_required
def settings(request):
    context = get_app_shell_context(current_page="settings")
    context["active_tab"] = "organization"
    
    if request.htmx:
        return render(request, "devopshero_app/settings/organization.html", context=context)
        
    context["content_url"] = "/settings/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def settings_organization(request):
    context = get_app_shell_context(current_page="settings")
    context["active_tab"] = "organization"
    
    if request.htmx:
        return render(request, "devopshero_app/settings/organization.html", context=context)
    
    context["content_url"] = "/settings/organization/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def settings_members(request):
    context = get_app_shell_context(current_page="settings")
    context["active_tab"] = "members"
    
    if request.htmx:
        return render(request, "devopshero_app/settings/members.html", context=context)
    
    context["content_url"] = "/settings/members/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def settings_aws_accounts(request):
    context = get_app_shell_context(current_page="settings")
    context["active_tab"] = "aws-accounts"
    
    # TODO: Filter by user's organization once org context is implemented
    context["aws_accounts"] = AWSAccount.objects.all()
    
    if request.htmx:
        return render(request, "devopshero_app/settings/aws_accounts.html", context=context)
    
    context["content_url"] = "/settings/aws-accounts/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def settings_aws_accounts_add(request):
    """Render the Add AWS Account modal dialog and handle account creation."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if name:
            # TODO: Get organization from user's current org context
            org = Organization.objects.first()  # Temporary: use first org
            
            aws_account = AWSAccount.objects.create(
                organization=org,
                name=name,
                created_by=request.user,
            )
            
            # Return empty response with HX-Trigger to open CloudFormation URL
            from django.http import HttpResponse
            response = HttpResponse("")
            response["HX-Trigger"] = f'{{"openCloudFormation": "{aws_account.get_cloudformation_url()}"}}'
            return response
    
    return render(request, "devopshero_app/settings/aws_account_add_modal.html")


@login_required
def settings_billing(request):
    context = get_app_shell_context(current_page="settings")
    context["active_tab"] = "billing"
    
    if request.htmx:
        return render(request, "devopshero_app/settings/billing.html", context=context)
    
    context["content_url"] = "/settings/billing/"
    return render(request, "devopshero_app/app_shell.html", context=context)

