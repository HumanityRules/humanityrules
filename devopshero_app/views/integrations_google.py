"""Google Workspace OAuth start view.

Step 1c of the Google Workspace integration (see
`docs/google_workspace_integration_design.md`). The authenticated DOH user lands
here carrying `?rd=<URL>` pointing at the Hermes WebUI in a customer env. We
validate `rd` against known env domains, stash state, and kick the browser off
to Google's consent screen.
"""

import json
import logging
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect

from devopshero_app.models import Environment, IntegrationConfig
from devopshero_app.services.infra_customer import iam_utils, secrets_utils

logger = logging.getLogger(__name__)


GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "email",
]


def _resolve_env_by_rd(rd: str) -> Environment | None:
    """Return the Environment whose shared_alb_hosted_zone suffixes *rd*'s host, or None.

    We accept any URL whose host is a subdomain of a known env's hosted zone —
    e.g. rd `https://hermes.dev.example.com/x` matches an Environment with
    `shared_alb_hosted_zone = "dev.example.com"`.
    """
    if not rd:
        return None
    parsed = urlparse(rd)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    for env in Environment.objects.exclude(shared_alb_hosted_zone="").only("id", "slug", "shared_alb_hosted_zone"):
        zone = env.shared_alb_hosted_zone.lower()
        if host == zone or host.endswith("." + zone):
            return env
    return None


@login_required
def integrations_google_start(request: HttpRequest) -> HttpResponse:
    """Validate `rd`, stash state, redirect to Google's OAuth consent screen."""
    rd = request.GET.get("rd", "")
    env = _resolve_env_by_rd(rd=rd)
    if env is None:
        return HttpResponseBadRequest("Invalid or unknown rd")

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
        "env_slug": env.slug,
        "username": request.user.username,
    }

    web = google_cfg.config
    params = urlencode({
        "client_id": web["client_id"],
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "redirect_uri": web["redirect_uris"][0],
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    })
    return redirect(f"{web['auth_uri']}?{params}")


def _exchange_google_code(web: dict, code: str) -> dict:
    """POST to Google's token endpoint and return the JSON body."""
    response = httpx.post(
        web["token_uri"],
        data={
            "client_id": web["client_id"],
            "client_secret": web["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": web["redirect_uris"][0],
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
def integrations_google_callback(request: HttpRequest) -> HttpResponse:
    """Exchange Google's auth code, write tokens to customer Secrets Manager, 302 back to `rd`."""
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
    env_slug = payload.get("env_slug", "")
    username = payload.get("username", "")
    if not rd or not env_slug or not username:
        return HttpResponseBadRequest("Corrupt session payload")

    # Defense in depth: the authenticated user must own the session payload.
    # Prevents a cross-user race from writing tokens under the wrong key.
    if username != request.user.username:
        logger.error(
            "google callback user mismatch session_user=%s request_user=%s",
            username, request.user.username,
        )
        return HttpResponseBadRequest("User mismatch")

    try:
        google_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
    except IntegrationConfig.DoesNotExist:
        logger.error("google callback failed: IntegrationConfig(provider=google) missing")
        return HttpResponseBadRequest("Google integration not configured")

    try:
        env = Environment.objects.select_related("aws_account").get(slug=env_slug)
    except Environment.DoesNotExist:
        logger.error("google callback failed: env_slug=%s not found", env_slug)
        return HttpResponseBadRequest("Environment not found")

    try:
        token_response = _exchange_google_code(web=google_cfg.config, code=code)
    except Exception as exc:
        logger.error("google token exchange failed: %s", exc)
        return HttpResponseBadRequest("Google token exchange failed")

    token_record = {
        "access_token": token_response["access_token"],
        "refresh_token": token_response.get("refresh_token", ""),
        "scope": token_response.get("scope", ""),
        "token_type": token_response.get("token_type", "Bearer"),
        "expires_at": int(time.time()) + int(token_response.get("expires_in", 0)),
        "granted_at": int(time.time()),
    }

    aws_session = iam_utils.get_assumed_role_session(
        access_key=None,
        secret_key=None,
        account_id=env.aws_account.aws_account_id,
        external_id=str(env.aws_account.external_id),
        region=env.aws_region,
    )
    secrets_utils.write_integration_tokens(
        session=aws_session,
        env_slug=env_slug,
        provider="google",
        username=username,
        tokens=token_record,
    )
    logger.info("google tokens stored env=%s user=%s", env_slug, username)

    return redirect(_append_query(rd, {"connected": "google"}))
