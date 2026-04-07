"""
GitHub App integration: OAuth user flow, installation picker, and webhooks.

Flow:
1. /github/connect → OAuth authorize redirect (stores org_id + CSRF state in session)
2. /github/callback → exchanges code for user token, lists user's installations,
   stores them in session, redirects to picker
3. /github/select-installation (GET) → shows picker page
4. /github/select-installation (POST) → connects selected installation to DOH org
5. If user clicks "Install on new org" → GitHub's /installations/new →
   /github/setup → connects the new installation
"""

import hashlib
import hmac
import json
import logging
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from devopshero_app.models import GitProviderIntegration, Organization
from devopshero_app.services import abac
from devopshero_app.services.gitproviders import github_client

from . import base

logger = logging.getLogger(__name__)


@login_required
def github_connect(request):
    """Start GitHub OAuth flow to discover the user's available installations."""
    org = request.user.current_organization
    if not abac.is_org_admin(organization=org, user=request.user):
        return HttpResponseForbidden("You must be an organization admin to connect GitHub.")

    state = secrets.token_urlsafe(32)
    request.session["github_connect_org_id"] = str(org.id)
    request.session["github_oauth_state"] = state

    authorize_url = github_client.get_oauth_authorize_url(state=state)
    return redirect(authorize_url)


@login_required
def github_callback(request):
    """Handle GitHub OAuth callback: exchange code, list installations, redirect to picker."""
    org = request.user.current_organization
    if not abac.is_org_admin(organization=org, user=request.user):
        return HttpResponseForbidden("You must be an organization admin to connect GitHub.")

    # Backwards compat: if GitHub sends installation_id here, delegate to setup handler
    if request.GET.get("installation_id"):
        return _handle_setup(request=request, org=org)

    code = request.GET.get("code")
    state = request.GET.get("state")

    if not code:
        logger.error("GitHub OAuth callback missing code parameter")
        return redirect("/integrations/git-integrations/?error=oauth_failed")

    expected_state = request.session.pop("github_oauth_state", None)
    if not expected_state or state != expected_state:
        logger.error("GitHub OAuth state mismatch: expected=%s, got=%s", expected_state, state)
        return redirect("/integrations/git-integrations/?error=oauth_failed")

    try:
        user_token = github_client.exchange_code_for_user_token(code=code)
        installations = github_client.list_user_installations(user_token=user_token)
    except Exception as e:
        logger.error("GitHub OAuth token exchange or installation listing failed: %s", str(e))
        return redirect("/integrations/git-integrations/?error=oauth_failed")

    if not installations:
        return redirect(github_client.get_app_installation_url())

    request.session["github_installations"] = [
        {
            "id": inst.id,
            "account_name": inst.account_name,
            "account_type": inst.account_type,
            "avatar_url": inst.avatar_url,
        }
        for inst in installations
    ]
    return redirect("/github/select-installation")


@login_required
def github_select_installation(request):
    """Show the installation picker (GET) or connect the selected installation (POST)."""
    org = request.user.current_organization
    if not abac.is_org_admin(organization=org, user=request.user):
        return HttpResponseForbidden("You must be an organization admin to connect GitHub.")

    if request.method == "POST":
        return _handle_select_installation_post(request=request, org=org)

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="integrations")
        context["content_url"] = "/github/select-installation"
        return render(request, "devopshero_app/app_shell.html", context=context)

    installations = request.session.get("github_installations", [])
    if not installations:
        return redirect("/integrations/git-integrations/")

    context = base.get_app_shell_context(request=request, current_page="integrations")
    context["installations"] = installations
    context["install_new_url"] = github_client.get_app_installation_url()
    return render(request, "devopshero_app/github/github_select_installation.html", context=context)


def _handle_select_installation_post(request, org: Organization) -> HttpResponse:
    """Process the user's installation choice from the picker form."""
    installation_id = request.POST.get("installation_id")
    if not installation_id:
        return HttpResponseBadRequest("Missing installation_id")

    request.session.pop("github_installations", None)

    org_id = request.session.pop("github_connect_org_id", None)
    if org_id and str(org.id) != org_id:
        logger.error("GitHub select-installation org mismatch: session=%s, current=%s", org_id, org.id)
        return HttpResponseBadRequest("Organization mismatch")

    return _connect_installation(org=org, installation_id=installation_id)


@login_required
def github_setup(request):
    """Handle GitHub App post-installation redirect (Setup URL)."""
    org = request.user.current_organization
    if not abac.is_org_admin(organization=org, user=request.user):
        return HttpResponseForbidden("You must be an organization admin to connect GitHub.")

    return _handle_setup(request=request, org=org)


def _handle_setup(request, org: Organization) -> HttpResponse:
    """Process a GitHub App installation callback with installation_id."""
    installation_id = request.GET.get("installation_id")
    if not installation_id:
        logger.error("GitHub setup callback missing installation_id")
        return HttpResponseBadRequest("Missing installation_id")

    request.session.pop("github_installations", None)

    org_id = request.session.pop("github_connect_org_id", None)
    if org_id and str(org.id) != org_id:
        logger.error("GitHub setup org mismatch: session=%s, current=%s", org_id, org.id)
        return HttpResponseBadRequest("Organization mismatch")

    return _connect_installation(org=org, installation_id=installation_id)


def _connect_installation(org: Organization, installation_id: str) -> HttpResponse:
    """Create/update GitProviderIntegration and sync repositories."""
    try:
        installation_details = github_client.get_installation_details(installation_id=installation_id)
        account_name = installation_details.get("account", {}).get("login", "Unknown")

        integration, created = GitProviderIntegration.objects.update_or_create(
            organization=org,
            provider=GitProviderIntegration.Provider.GITHUB,
            defaults={
                "installation_id": installation_id,
                "status": GitProviderIntegration.Status.CONNECTED,
            },
        )

        if created:
            logger.info("Created GitHub integration for org %s (installation_id=%s, account=%s)", org.name, installation_id, account_name)
        else:
            logger.info("Updated GitHub integration for org %s (installation_id=%s, account=%s)", org.name, installation_id, account_name)

        sync_result = github_client.sync_repositories(organization=org, integration=integration)
        logger.info(
            "Synced repos for org %s: added=%d, updated=%d, removed=%d",
            org.name,
            sync_result.added,
            sync_result.updated,
            sync_result.removed,
        )

    except Exception as e:
        logger.error("GitHub connection failed: %s", str(e))
        GitProviderIntegration.objects.update_or_create(
            organization=org,
            provider=GitProviderIntegration.Provider.GITHUB,
            defaults={
                "installation_id": installation_id,
                "status": GitProviderIntegration.Status.ERROR,
            },
        )
        return redirect("/integrations/git-integrations/?error=connection_failed")

    return redirect("/integrations/git-integrations/")


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

from django.views.decorators.csrf import csrf_exempt  # noqa: E402


@csrf_exempt
@require_POST
def github_webhook(request):
    """Handle GitHub webhook events (push, installation, etc.)."""
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_webhook_signature(payload=request.body, signature=signature):
        logger.error("GitHub webhook signature verification failed")
        return HttpResponse(status=401)

    event_type = request.headers.get("X-GitHub-Event")
    delivery_id = request.headers.get("X-GitHub-Delivery")

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        logger.error("GitHub webhook invalid JSON payload")
        return HttpResponseBadRequest("Invalid JSON")

    logger.info("GitHub webhook received: event=%s, delivery=%s", event_type, delivery_id)

    if event_type == "push":
        _handle_push_event(payload=payload)
    elif event_type == "installation":
        _handle_installation_event(payload=payload)
    elif event_type == "installation_repositories":
        _handle_installation_repositories_event(payload=payload)
    elif event_type == "ping":
        logger.info("GitHub webhook ping received")
    else:
        logger.info("GitHub webhook event %s ignored", event_type)

    return HttpResponse(status=200)


def _verify_webhook_signature(payload: bytes, signature: str | None) -> bool:
    """Verify the GitHub webhook signature."""
    if not signature or not settings.GITHUB_WEBHOOK_SECRET:
        return True

    expected = "sha256=" + hmac.new(
        key=settings.GITHUB_WEBHOOK_SECRET.encode(),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def _handle_push_event(payload: dict):
    """Handle push events - trigger deployment if configured."""
    ref = payload.get("ref", "")
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    pusher = payload.get("pusher", {}).get("name", "unknown")

    logger.info("Push event: repo=%s, ref=%s, pusher=%s", repo_full_name, ref, pusher)

    # TODO: Implement auto-deploy logic
    # 1. Find Repository by full_name
    # 2. Find Apps linked to this repository
    # 3. Check if ref matches the default branch
    # 4. Trigger deployment for matching apps


def _handle_installation_event(payload: dict):
    """Handle installation events (installed, uninstalled, etc.)."""
    action = payload.get("action")
    installation_id = payload.get("installation", {}).get("id")

    logger.info("Installation event: action=%s, installation_id=%s", action, installation_id)

    if action == "deleted":
        GitProviderIntegration.objects.filter(
            installation_id=str(installation_id),
        ).update(status=GitProviderIntegration.Status.ERROR)


def _handle_installation_repositories_event(payload: dict):
    """Handle installation_repositories events (repos added/removed)."""
    action = payload.get("action")
    installation_id = payload.get("installation", {}).get("id")

    logger.info("Installation repositories event: action=%s, installation_id=%s", action, installation_id)

    try:
        integration = GitProviderIntegration.objects.get(installation_id=str(installation_id))
        github_client.sync_repositories(organization=integration.organization, integration=integration)
    except GitProviderIntegration.DoesNotExist:
        logger.error("No integration found for installation_id=%s", installation_id)
