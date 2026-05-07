"""Google Workspace OAuth start view.

Step 1c of the Google Workspace integration (see
`docs/google_workspace_integration_design.md`). The authenticated DOH user lands
here carrying `?rd=<URL>` pointing at the Hermes WebUI in a customer env. We
validate `rd` against known env domains, stash state, and kick the browser off
to Google's consent screen.
"""

import logging
import secrets
from urllib.parse import urlencode, urlparse

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect

from devopshero_app.models import Environment, IntegrationConfig

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
