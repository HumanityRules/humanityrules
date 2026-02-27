import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ..models import AWSAccount, GitProviderIntegration, Repository
from .base import get_app_shell_context

logger = logging.getLogger(__name__)


@login_required
def settings(request):
    context = get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "organization"
    
    if request.htmx:
        return render(request, "devopshero_app/settings/organization.html", context=context)
        
    context["content_url"] = "/settings/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def settings_organization(request):
    context = get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "organization"
    
    if request.htmx:
        return render(request, "devopshero_app/settings/organization.html", context=context)
    
    context["content_url"] = "/settings/organization/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def settings_aws_accounts(request):
    if not request.htmx:
        context = get_app_shell_context(request=request, current_page="settings")
        context["content_url"] = "/settings/aws-accounts/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    org = request.user.current_organization

    context = get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "aws-accounts"
    context["aws_accounts"] = AWSAccount.objects.filter(organization=org)

    return render(request, "devopshero_app/settings/aws_accounts.html", context=context)


@login_required
def settings_aws_accounts_add(request):
    """Render the Add AWS Account modal dialog and handle account creation."""
    from django.http import HttpResponse
    
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        
        # Validate name is provided
        if not name:
            response = HttpResponse("")
            response["HX-Trigger"] = '{"validationError": "Please enter an AWS account name"}'
            return response
        
        org = request.user.current_organization
        
        # Check if the AWS account name already exists for this organization
        existing = AWSAccount.objects.filter(organization=org, name=name).first()
        if existing:
            if existing.status in (AWSAccount.Status.PENDING, AWSAccount.Status.ERROR):
                # Allow re-clicking for pending/error accounts
                response = HttpResponse("")
                response["HX-Trigger"] = f'{{"openCloudFormation": "{existing.get_cloudformation_url()}"}}'
                return response
            else:
                # Account is already connected
                response = HttpResponse("")
                response["HX-Trigger"] = '{"validationError": "An AWS account with this name is already connected"}'
                return response
        
        # Create new account
        aws_account = AWSAccount.objects.create(
            organization=org,
            name=name,
            created_by=request.user,
        )
        
        # Return empty response with HX-Trigger to open CloudFormation URL
        response = HttpResponse("")
        response["HX-Trigger"] = f'{{"openCloudFormation": "{aws_account.get_cloudformation_url()}"}}'
        return response
    
    return render(request, "devopshero_app/settings/aws_account_add_modal.html")


@login_required
def settings_billing(request):
    context = get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "billing"
    
    if request.htmx:
        return render(request, "devopshero_app/settings/billing.html", context=context)
    
    context["content_url"] = "/settings/billing/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def settings_git_integrations(request):
    """Render the Git Integrations settings tab and handle sync requests."""
    if not request.htmx:
        context = get_app_shell_context(request=request, current_page="settings")
        context["content_url"] = "/settings/git-integrations/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    from devopshero_app.services.gitproviders import github_client

    org = request.user.current_organization

    # Handle POST request for re-sync
    if request.method == "POST":
        integration = GitProviderIntegration.objects.filter(
            organization=org,
            provider=GitProviderIntegration.Provider.GITHUB,
        ).first()

        if integration and integration.installation_id:
            try:
                github_client.sync_repositories(organization=org, integration=integration)
                # Sync succeeded — ensure status is CONNECTED
                if integration.status != GitProviderIntegration.Status.CONNECTED:
                    integration.status = GitProviderIntegration.Status.CONNECTED
                    integration.save(update_fields=["status", "updated_at"])
            except Exception as e:
                logger.error("GitHub re-sync failed: %s", str(e))
                integration.status = GitProviderIntegration.Status.ERROR
                integration.save(update_fields=["status", "updated_at"])

    # Get GitHub integration for this org
    github_integration = GitProviderIntegration.objects.filter(
        organization=org,
        provider=GitProviderIntegration.Provider.GITHUB,
    ).first()

    context = get_app_shell_context(request=request, current_page="settings")
    context["active_tab"] = "git-integrations"
    context["github_integration"] = github_integration

    if github_integration:
        repositories = Repository.objects.filter(integration=github_integration).order_by("full_name")
        context["repositories"] = repositories
        context["repo_count"] = repositories.count()

    return render(request, "devopshero_app/settings/git_integrations.html", context=context)

