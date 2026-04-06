"""
GitHub App OAuth flow and webhook handling.
"""

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import GitProviderIntegration
from devopshero_app.services import abac
from devopshero_app.services.gitproviders import github_client

logger = logging.getLogger(__name__)


@login_required
def github_connect(request):
    """Redirect user to GitHub App installation page."""
    org = request.user.current_organization
    if not abac.is_org_admin(organization=org, user=request.user):
        return HttpResponseForbidden("You must be an organization admin to connect GitHub.")

    # Store the organization ID in session so we know which org to connect on callback
    request.session["github_connect_org_id"] = str(org.id)

    installation_url = github_client.get_app_installation_url()
    return redirect(installation_url)


@login_required
def github_callback(request):
    """Handle GitHub App installation callback."""
    org = request.user.current_organization
    if not abac.is_org_admin(organization=org, user=request.user):
        return HttpResponseForbidden("You must be an organization admin to connect GitHub.")

    installation_id = request.GET.get("installation_id")
    setup_action = request.GET.get("setup_action")

    if not installation_id:
        logger.error("GitHub callback missing installation_id")
        return HttpResponseBadRequest("Missing installation_id")

    # Get the organization from session
    org_id = request.session.pop("github_connect_org_id", None)
    if not org_id:
        # Fall back to current organization if session expired
        org = request.user.current_organization
    else:
        org = request.user.current_organization
        # Verify the org ID matches (security check)
        if str(org.id) != org_id:
            logger.error("GitHub callback org mismatch: session=%s, current=%s", org_id, org.id)
            return HttpResponseBadRequest("Organization mismatch")

    try:
        # Get installation details from GitHub
        installation_details = github_client.get_installation_details(installation_id=installation_id)
        account_name = installation_details.get("account", {}).get("login", "Unknown")

        # Create or update the GitProviderIntegration
        integration, created = GitProviderIntegration.objects.update_or_create(
            organization=org,
            provider=GitProviderIntegration.Provider.GITHUB,
            defaults={
                "installation_id": installation_id,
                "status": GitProviderIntegration.Status.CONNECTED,
            },
        )

        if created:
            logger.info("Created GitHub integration for org %s (installation_id=%s)", org.name, installation_id)
        else:
            logger.info("Updated GitHub integration for org %s (installation_id=%s)", org.name, installation_id)

        # Sync repositories
        sync_result = github_client.sync_repositories(organization=org, integration=integration)
        logger.info(
            "Synced repos for org %s: added=%d, updated=%d, removed=%d",
            org.name,
            sync_result.added,
            sync_result.updated,
            sync_result.removed,
        )

    except Exception as e:
        logger.error("GitHub callback failed: %s", str(e))
        # Create integration in error state
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


@csrf_exempt
@require_POST
def github_webhook(request):
    """Handle GitHub webhook events (push, installation, etc.)."""
    # Verify webhook signature
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_webhook_signature(request.body, signature):
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

    # Handle different event types
    if event_type == "push":
        _handle_push_event(payload)
    elif event_type == "installation":
        _handle_installation_event(payload)
    elif event_type == "installation_repositories":
        _handle_installation_repositories_event(payload)
    elif event_type == "ping":
        logger.info("GitHub webhook ping received")
    else:
        logger.info("GitHub webhook event %s ignored", event_type)

    return HttpResponse(status=200)


def _verify_webhook_signature(payload: bytes, signature: str | None) -> bool:
    """Verify the GitHub webhook signature."""
    if not signature or not settings.GITHUB_WEBHOOK_SECRET:
        # Skip verification if no secret configured (development)
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
        # Mark integration as disconnected
        GitProviderIntegration.objects.filter(
            installation_id=str(installation_id),
        ).update(status=GitProviderIntegration.Status.ERROR)


def _handle_installation_repositories_event(payload: dict):
    """Handle installation_repositories events (repos added/removed)."""
    action = payload.get("action")
    installation_id = payload.get("installation", {}).get("id")

    logger.info("Installation repositories event: action=%s, installation_id=%s", action, installation_id)

    # Re-sync repositories for this installation
    try:
        integration = GitProviderIntegration.objects.get(installation_id=str(installation_id))
        github_client.sync_repositories(organization=integration.organization, integration=integration)
    except GitProviderIntegration.DoesNotExist:
        logger.error("No integration found for installation_id=%s", installation_id)
