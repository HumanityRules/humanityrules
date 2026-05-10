import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from ...models import AWSAccount, IntegrationGitProvider, Repository
from .. import base

logger = logging.getLogger(__name__)


@login_required
def integrations_root(request: HttpRequest) -> HttpResponse:
    """Redirect /integrations/ to the default tab (AWS accounts)."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden
    return HttpResponseRedirect("/integrations/aws-accounts/")


@login_required
def integrations_aws_accounts(request: HttpRequest) -> HttpResponse:
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="integrations")
        context["content_url"] = "/integrations/aws-accounts/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    org = request.user.current_organization

    context = base.get_app_shell_context(request=request, current_page="integrations")
    context["active_tab"] = "aws-accounts"
    context["aws_accounts"] = AWSAccount.objects.filter(organization=org)

    return render(request, "devopshero_app/integrations/aws_accounts.html", context=context)


@login_required
def integrations_aws_accounts_add(request: HttpRequest) -> HttpResponse:
    """Render the Add AWS Account modal dialog and handle account creation."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    if request.method == "POST":
        name = request.POST.get("name", "").strip()

        if not name:
            response = HttpResponse("")
            response["HX-Trigger"] = '{"validationError": "Please enter an AWS account name"}'
            return response

        org = request.user.current_organization

        existing = AWSAccount.objects.filter(organization=org, name=name).first()
        if existing:
            if existing.status in (AWSAccount.Status.PENDING, AWSAccount.Status.ERROR):
                response = HttpResponse("")
                response["HX-Trigger"] = f'{{"openCloudFormation": "{existing.get_cloudformation_url()}"}}'
                return response
            else:
                response = HttpResponse("")
                response["HX-Trigger"] = '{"validationError": "An AWS account with this name is already connected"}'
                return response

        aws_account = AWSAccount.objects.create(
            organization=org,
            name=name,
            created_by=request.user,
        )

        response = HttpResponse("")
        response["HX-Trigger"] = f'{{"openCloudFormation": "{aws_account.get_cloudformation_url()}"}}'
        return response

    return render(request, "devopshero_app/integrations/aws_account_add_modal.html")


@login_required
def integrations_git_integrations(request: HttpRequest) -> HttpResponse:
    """Render the Git Integrations tab and handle sync requests."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="integrations")
        context["content_url"] = "/integrations/git-integrations/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    from devopshero_app.services.gitproviders import github_client

    org = request.user.current_organization

    if request.method == "POST":
        integration = IntegrationGitProvider.objects.filter(
            organization=org,
            provider=IntegrationGitProvider.Provider.GITHUB,
        ).first()

        if integration and integration.installation_id:
            try:
                github_client.sync_repositories(organization=org, integration=integration)
                if integration.status != IntegrationGitProvider.Status.CONNECTED:
                    integration.status = IntegrationGitProvider.Status.CONNECTED
                    integration.save(update_fields=["status", "updated_at"])
            except Exception as e:
                logger.error("GitHub re-sync failed: %s", str(e))
                integration.status = IntegrationGitProvider.Status.ERROR
                integration.save(update_fields=["status", "updated_at"])

    github_integration = IntegrationGitProvider.objects.filter(
        organization=org,
        provider=IntegrationGitProvider.Provider.GITHUB,
    ).first()

    context = base.get_app_shell_context(request=request, current_page="integrations")
    context["active_tab"] = "git-integrations"
    context["github_integration"] = github_integration

    if github_integration:
        repositories = Repository.objects.filter(integration=github_integration).order_by("full_name")
        context["repositories"] = repositories
        context["repo_count"] = repositories.count()

    return render(request, "devopshero_app/integrations/git_integrations.html", context=context)
