"""Google Workspace OAuth start + callback views.

See `docs/integrations_broker_design.md`. The authenticated DOH user
starts at `/integrations/google/start?rd=<URL>` (where `rd` points at the
Hermes WebUI in a customer env), consents at Google, and lands back at
`/integrations/google/callback`. The callback persists the refresh_token in
DOH's DB as an IntegrationUserCredential row; no long-lived Google credentials
cross into the customer env.
"""

import logging
import secrets
from urllib.parse import urlencode, urlparse

import httpx
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils import timezone

from devopshero_app.models import App, Environment, IntegrationConfig, IntegrationUserCredential, ResourceTag, User

logger = logging.getLogger(__name__)


GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "openid",
    "email",
]


def _pick_redirect_uri(request: HttpRequest, configured: list[str]) -> str:
    """Return the registered redirect URI whose host matches *request*'s host.

    Google validates the `redirect_uri` parameter exactly — it must be byte-identical
    to one of the OAuth client's registered URIs, AND it must be byte-identical at
    token-exchange time to what we sent on /authorize. Both the start view (browser)
    and the callback view (browser round-trip from Google) hit the same host, so
    we can recompute the choice on each step by looking at request.get_host().
    """
    request_host = request.get_host().lower()
    for uri in configured:
        if urlparse(uri).netloc.lower() == request_host:
            return uri
    raise ValueError(
        f"No redirect_uri configured for host {request_host!r}. Available: {configured!r}"
    )


def _resolve_env_by_rd(rd: str, user: User) -> Environment | None:
    """Return the Environment whose shared_alb_hosted_zone suffixes *rd*'s host, or None.

    We accept any URL whose host is a subdomain of a known env's hosted zone —
    e.g. rd `https://hermes.dev.example.com/x` matches an Environment with
    `shared_alb_hosted_zone = "dev.example.com"`. Scoped to envs in orgs *user*
    is a member of.
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


@login_required
def integrations_google_oauth_start(request: HttpRequest) -> HttpResponse:
    """Validate `rd`, stash state, redirect to Google's OAuth consent screen."""
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

    try:
        google_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
    except IntegrationConfig.DoesNotExist:
        logger.error("google integration start failed: IntegrationConfig(provider=google) missing")
        return HttpResponseBadRequest(
            "Google integration not configured. Run: uv run manage.py setup_google_oauth_client --file <json>"
        )

    state = secrets.token_urlsafe(32)
    request.session["google_oauth_state"] = state
    request.session["google_oauth_payload"] = {
        "rd": rd,
        "env_id": str(env.id),
        "app_slug": app_slug,
        "owner_username": request.user.username,
    }

    web = google_cfg.config
    try:
        redirect_uri = _pick_redirect_uri(request=request, configured=web["redirect_uris"])
    except ValueError as exc:
        logger.error("google oauth start failed: %s", exc)
        return HttpResponseBadRequest(
            "Google OAuth client has no redirect_uri registered for this host."
        )

    params = urlencode({
        "client_id": web["client_id"],
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "redirect_uri": redirect_uri,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    })
    return redirect(f"{web['auth_uri']}?{params}")


def _exchange_google_code(web: dict, code: str, redirect_uri: str) -> dict:
    """POST to Google's token endpoint and return the JSON body.

    *redirect_uri* must be byte-identical to what was sent on the /authorize step;
    Google rejects mismatches with invalid_grant.
    """
    response = httpx.post(
        web["token_uri"],
        data={
            "client_id": web["client_id"],
            "client_secret": web["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _append_query(url: str, extra: dict[str, str]) -> str:
    parsed = urlparse(url)
    existing = parsed.query
    encoded = urlencode(extra)
    combined = f"{existing}&{encoded}" if existing else encoded
    return parsed._replace(query=combined).geturl()


@login_required
def integrations_google_oauth_callback(request: HttpRequest) -> HttpResponse:
    """Exchange Google's auth code, persist refresh_token on DOH, 302 back to `rd`."""
    if request.GET.get("error"):
        logger.error("google oauth callback error=%s", request.GET.get("error"))
        return HttpResponseBadRequest(f"Google OAuth error: {request.GET['error']}")

    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    if not code or not state:
        return HttpResponseBadRequest("Missing code or state")

    expected_state = request.session.pop("google_oauth_state", "")
    payload = request.session.pop("google_oauth_payload", None)
    if not expected_state or state != expected_state or not isinstance(payload, dict):
        return HttpResponseBadRequest("Invalid state")

    rd = payload.get("rd", "")
    env_id = payload.get("env_id", "")
    app_slug = payload.get("app_slug", "")
    owner_username = payload.get("owner_username", "")
    if not rd or not env_id or not app_slug or not owner_username:
        return HttpResponseBadRequest("Corrupt session payload")

    # Defense in depth: the authenticated user must own the session payload.
    # Prevents a cross-user race from writing the row under the wrong owner.
    if owner_username != request.user.username:
        logger.error(
            "google callback user mismatch session_user=%s request_user=%s",
            owner_username, request.user.username,
        )
        return HttpResponseBadRequest("User mismatch")

    try:
        google_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
    except IntegrationConfig.DoesNotExist:
        logger.error("google callback failed: IntegrationConfig(provider=google) missing")
        return HttpResponseBadRequest("Google integration not configured")

    try:
        env = Environment.objects.get(id=env_id)
    except Environment.DoesNotExist:
        logger.error("google callback failed: env_id=%s not found", env_id)
        return HttpResponseBadRequest("Environment not found")

    try:
        redirect_uri = _pick_redirect_uri(
            request=request, configured=google_cfg.config["redirect_uris"],
        )
    except ValueError as exc:
        logger.error("google callback failed: %s", exc)
        return HttpResponseBadRequest(
            "Google OAuth client has no redirect_uri registered for this host."
        )

    try:
        token_response = _exchange_google_code(
            web=google_cfg.config, code=code, redirect_uri=redirect_uri,
        )
    except Exception as exc:
        logger.error("google token exchange failed: %s", exc)
        return HttpResponseBadRequest("Google token exchange failed")

    # Google issues a new refresh_token only on the first consent with
    # `prompt=consent` (or when previously revoked); subsequent grants may
    # omit it. Reject when absent — without a refresh_token we can't serve
    # access tokens to env-resident callers.
    refresh_token = token_response.get("refresh_token", "")
    if not refresh_token:
        logger.error(
            "google token exchange returned no refresh_token env=%s user=%s",
            env.slug, owner_username,
        )
        return HttpResponseBadRequest(
            "Google did not return a refresh_token. Revoke the app at "
            "myaccount.google.com and reconnect."
        )

    IntegrationUserCredential.objects.update_or_create(
        owner_user=request.user,
        environment=env,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.GOOGLE,
        defaults={
            "credentials": {"refresh_token": refresh_token},
            "config": {"scope": token_response.get("scope", "")},
            "metadata": {"connected_at": timezone.now().isoformat()},
            "last_refreshed_at": None,
        },
    )
    logger.info(
        "google integration stored env=%s owner=%s app=%s",
        env.slug, owner_username, app_slug,
    )

    return redirect(_append_query(rd, {"connected": "google"}))


def _revoke_google_refresh_token(refresh_token: str) -> None:
    """Best-effort revoke at Google's oauth2 endpoint.

    Failure doesn't block the local deletion — the row removal is the
    load-bearing step. Revoke just accelerates Google-side cleanup so a
    subsequent refresh would 410 instead of silently succeeding in an edge
    case where we somehow missed the local delete.
    """
    try:
        httpx.post(
            "https://oauth2.googleapis.com/revoke",
            data={"token": refresh_token},
            timeout=10,
        )
    except Exception as exc:
        logger.error("google revoke best-effort failed: %s", exc)


@login_required
def integrations_google_oauth_disconnect(request: HttpRequest) -> HttpResponse:
    """Disconnect the authenticated user's Google grant for the env resolved from `rd`.

    Idempotent: returns 302 to `rd?disconnected=google` whether a row existed
    or not. The WebUI extension re-fetches /__doh_broker/integrations after
    redirect; the broker force-refreshes on that GET so the Integrations pane
    reflects the disconnect immediately.
    """
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
        provider=IntegrationUserCredential.Provider.GOOGLE,
    ).first()
    if integration is not None:
        refresh_token = integration.credentials.get("refresh_token", "")
        integration.delete()
        if refresh_token:
            _revoke_google_refresh_token(refresh_token=refresh_token)
        logger.info(
            "google integration disconnected env=%s owner=%s app=%s",
            env.slug, request.user.username, app_slug,
        )
    else:
        logger.info(
            "google disconnect no-op (no grant) env=%s owner=%s app=%s",
            env.slug, request.user.username, app_slug,
        )

    return redirect(_append_query(rd, {"disconnected": "google"}))
