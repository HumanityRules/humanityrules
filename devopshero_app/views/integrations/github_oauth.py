"""GitHub OAuth start + callback views (per-user, runtime-scoped).

Distinct from `views/github.py`, which handles the org-admin GitHub App
*installation* flow used by the DOH control plane to enumerate repos. This
file is the per-user OAuth dance: an end user inside a Hermes WebUI clicks
"Connect GitHub", consents at github.com, and a refresh_token is persisted
on DOH as an IntegrationUserCredential row.

We reuse the existing GitHub App's `client_id`/`client_secret` because a
GitHub App can issue user-to-server tokens via the same OAuth endpoints. The
"Expire user authorization tokens" toggle on the App must be ON, so each
user authorization yields an 8h access_token + 6mo refresh_token.

User-to-server tokens act as the authorizing user (commits attribute to
them), but a token's effective access on a repo is gated by whether the App
is installed on the owning account/org. GitHub stitches authorize+install
into one flow when the user authorizes from an account that hasn't
installed the App — no separate install step required from the WebUI.
"""

import logging
import secrets
from urllib.parse import urlencode, urlparse

import httpx
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils import timezone

from devopshero_app.models import App, Environment, IntegrationUserCredential, ResourceTag, User


logger = logging.getLogger(__name__)


GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 30


def _resolve_env_by_rd(rd: str, user: User) -> Environment | None:
    """Return the Environment whose shared_alb_hosted_zone suffixes rd's host, or None.

    Scoped to envs in orgs *user* is a member of.
    """
    if not rd:
        return None
    parsed = urlparse(rd)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    candidates = (
        Environment.objects
        .exclude(shared_alb_hosted_zone="")
        .filter(aws_account__organization__memberships__user=user)
        .only("id", "slug", "shared_alb_hosted_zone")
    )
    for env in candidates:
        zone = env.shared_alb_hosted_zone.lower()
        if host == zone or host.endswith("." + zone):
            return env
    return None


def _resolve_owned_app_slug(app_slug: str, env: Environment, owner_username: str) -> str | None:
    """Return app_slug when it identifies an app owned by the user in env's org."""
    if not app_slug:
        return None
    app = App.objects.filter(
        organization=env.aws_account.organization,
        slug=app_slug,
    ).first()
    if app is None:
        return None
    owner_tag = ResourceTag.objects.filter(
        resource_type=ResourceTag.ResourceType.APP,
        app=app,
        key="owner",
        value=owner_username,
    ).first()
    return app.slug if owner_tag is not None else None


def _redirect_uri(request: HttpRequest) -> str:
    """Build the absolute callback URL for this host.

    GitHub validates `redirect_uri` against the App's Callback URL list.
    Each customer-facing DOH host where users may connect must be listed
    on the GitHub App's settings page.
    """
    return request.build_absolute_uri("/integrations/github/callback/")


@login_required
def integrations_github_oauth_start(request: HttpRequest) -> HttpResponse:
    """Validate `rd`, stash state, redirect to GitHub's OAuth consent screen."""
    rd = request.GET.get("rd", "")
    env = _resolve_env_by_rd(rd=rd, user=request.user)
    if env is None:
        return HttpResponseBadRequest("Invalid or unknown rd")
    app_slug = _resolve_owned_app_slug(
        app_slug=request.GET.get("app_slug", ""),
        env=env,
        owner_username=request.user.username,
    )
    if app_slug is None:
        return HttpResponseBadRequest("Invalid or unauthorized app_slug")

    if not settings.GITHUB_APP_CLIENT_ID:
        logger.error("github oauth start failed: GITHUB_APP_CLIENT_ID not configured")
        return HttpResponseBadRequest("GitHub integration not configured.")

    state = secrets.token_urlsafe(32)
    request.session["github_user_oauth_state"] = state
    request.session["github_user_oauth_payload"] = {
        "rd": rd,
        "env_id": str(env.id),
        "app_slug": app_slug,
        "owner_username": request.user.username,
    }

    params = urlencode({
        "client_id": settings.GITHUB_APP_CLIENT_ID,
        "redirect_uri": _redirect_uri(request=request),
        "state": state,
    })
    return redirect(f"{GITHUB_AUTHORIZE_URL}?{params}")


def _exchange_github_code(code: str, redirect_uri: str) -> dict:
    """POST to GitHub's token endpoint and return the parsed JSON body.

    GitHub's token endpoint defaults to a urlencoded response — we set
    Accept: application/json to get a normal JSON body.
    """
    response = httpx.post(
        GITHUB_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.GITHUB_APP_CLIENT_ID,
            "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=GITHUB_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _append_query(url: str, extra: dict[str, str]) -> str:
    parsed = urlparse(url)
    encoded = urlencode(extra)
    combined = f"{parsed.query}&{encoded}" if parsed.query else encoded
    return parsed._replace(query=combined).geturl()


@login_required
def integrations_github_oauth_callback(request: HttpRequest) -> HttpResponse:
    """Exchange GitHub's auth code, persist refresh_token, 302 back to `rd`."""
    if request.GET.get("error"):
        logger.error("github oauth callback error=%s", request.GET.get("error"))
        return HttpResponseBadRequest(f"GitHub OAuth error: {request.GET['error']}")

    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    if not code or not state:
        return HttpResponseBadRequest("Missing code or state")

    expected_state = request.session.pop("github_user_oauth_state", "")
    payload = request.session.pop("github_user_oauth_payload", None)
    if not expected_state or state != expected_state or not isinstance(payload, dict):
        return HttpResponseBadRequest("Invalid state")

    rd = payload.get("rd", "")
    env_id = payload.get("env_id", "")
    app_slug = payload.get("app_slug", "")
    owner_username = payload.get("owner_username", "")
    if not rd or not env_id or not app_slug or not owner_username:
        return HttpResponseBadRequest("Corrupt session payload")

    if owner_username != request.user.username:
        logger.error(
            "github callback user mismatch session_user=%s request_user=%s",
            owner_username, request.user.username,
        )
        return HttpResponseBadRequest("User mismatch")

    if not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        logger.error("github callback failed: GITHUB_APP_CLIENT_ID/SECRET not configured")
        return HttpResponseBadRequest("GitHub integration not configured")

    try:
        env = Environment.objects.get(id=env_id)
    except Environment.DoesNotExist:
        logger.error("github callback failed: env_id=%s not found", env_id)
        return HttpResponseBadRequest("Environment not found")

    try:
        token_response = _exchange_github_code(code=code, redirect_uri=_redirect_uri(request=request))
    except Exception as exc:
        logger.error("github token exchange failed: %s", exc)
        return HttpResponseBadRequest("GitHub token exchange failed")

    if "error" in token_response:
        logger.error(
            "github token exchange returned error=%s description=%s",
            token_response.get("error"), token_response.get("error_description"),
        )
        return HttpResponseBadRequest(
            f"GitHub token exchange failed: {token_response.get('error_description', token_response['error'])}"
        )

    # Without "Expire user authorization tokens" enabled on the GitHub App,
    # GitHub returns access_token only — we'd have nothing to refresh from.
    # Reject loudly so a misconfigured App is caught at connect time, not in
    # the broker eight hours later.
    refresh_token = token_response.get("refresh_token", "")
    access_token = token_response.get("access_token", "")
    if not refresh_token or not access_token:
        logger.error(
            "github token exchange missing tokens env=%s owner=%s has_access=%s has_refresh=%s",
            env.slug, owner_username, bool(access_token), bool(refresh_token),
        )
        return HttpResponseBadRequest(
            "GitHub did not return a refresh token. The DOH GitHub App must have "
            "'Expire user authorization tokens' enabled."
        )

    IntegrationUserCredential.objects.update_or_create(
        owner_user=request.user,
        environment=env,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.GITHUB,
        defaults={
            "credentials": {"refresh_token": refresh_token},
            "config": {"scope": token_response.get("scope", "")},
            "metadata": {"connected_at": timezone.now().isoformat()},
            "last_refreshed_at": None,
        },
    )
    logger.info(
        "github integration stored env=%s owner=%s app=%s",
        env.slug, owner_username, app_slug,
    )

    return redirect(_append_query(url=rd, extra={"connected": "github"}))


def _revoke_github_grant(refresh_token: str) -> None:
    """Best-effort revoke at GitHub's grants endpoint.

    Failure doesn't block the local deletion — the row removal is the
    load-bearing step.
    """
    if not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        return
    try:
        httpx.delete(
            f"{GITHUB_API_BASE}/applications/{settings.GITHUB_APP_CLIENT_ID}/grant",
            auth=(settings.GITHUB_APP_CLIENT_ID, settings.GITHUB_APP_CLIENT_SECRET),
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            json={"access_token": refresh_token},
            timeout=10,
        )
    except Exception as exc:
        logger.error("github revoke best-effort failed: %s", exc)


@login_required
def integrations_github_oauth_disconnect(request: HttpRequest) -> HttpResponse:
    """Disconnect the authenticated user's GitHub grant for the env resolved from `rd`."""
    rd = request.GET.get("rd", "")
    env = _resolve_env_by_rd(rd=rd, user=request.user)
    if env is None:
        return HttpResponseBadRequest("Invalid or unknown rd")

    app_slug = _resolve_owned_app_slug(
        app_slug=request.GET.get("app_slug", ""),
        env=env,
        owner_username=request.user.username,
    )
    if app_slug is None:
        return HttpResponseBadRequest("Invalid or unauthorized app_slug")
    integration = IntegrationUserCredential.objects.filter(
        owner_user=request.user,
        environment=env,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.GITHUB,
    ).first()
    if integration is not None:
        refresh_token = integration.credentials.get("refresh_token", "")
        integration.delete()
        if refresh_token:
            _revoke_github_grant(refresh_token=refresh_token)
        logger.info(
            "github integration disconnected env=%s owner=%s app=%s",
            env.slug, request.user.username, app_slug,
        )
    else:
        logger.info(
            "github disconnect no-op (no grant) env=%s owner=%s app=%s",
            env.slug, request.user.username, app_slug,
        )

    return redirect(_append_query(url=rd, extra={"disconnected": "github"}))
