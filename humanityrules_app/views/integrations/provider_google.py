"""Google Workspace per-user integration: OAuth connect + token refresh.

Connect (browser redirect dance): the authenticated DOH user starts at
`/integrations/user/google/start/?rd=<URL>` (where `rd` points at the Hermes
WebUI in a customer env), consents at Google, and lands back at
`/integrations/user/google/callback/`. The callback persists the
refresh_token in DOH's DB as an IntegrationUserCredential row; no long-lived
Google credentials cross into the customer env.

Refresh (`refresh_outcome`): exchanges the stored refresh_token with Google
using DOH's OAuth client_secret and returns a broker-shaped outcome dict. The
batched refresh endpoint (`token_refresh_batch.py`) calls this from a worker
thread alongside the other providers. The refresh_token and DOH's
client_secret never cross the customer/DOH boundary; if Google has revoked
the refresh_token the row is deleted and the outcome flips to `absent` so the
WebUI prompts a reconnect.

See `docs/integrations/integrations_broker_design.md`.
"""

import logging
import secrets
from urllib.parse import urlencode, urlparse

import httpx
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils import timezone

from humanityrules_app.models import Environment, IntegrationConfig, IntegrationUserCredential, User
from humanityrules_app.views.integrations import provider_common

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

# Google's refresh-token exchange is a single small POST. Healthy P99 is
# well under 1s; setting the ceiling at 5s means the broker's batch refresh
# (which runs all providers in parallel) is naturally bounded by the slowest
# single exchange, no separate batch deadline needed. If upstream is taking
# longer than 5s, surfacing `transient` (cache-preserving) beats hanging a
# user request on a refresh that's about to fail anyway.
GOOGLE_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5


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


@login_required
def integrations_user_google_start(request: HttpRequest) -> HttpResponse:
    """Validate `rd`, stash state, redirect to Google's OAuth consent screen."""
    rd = request.GET.get("rd", "")
    env = provider_common.resolve_env_by_rd(rd=rd, user=request.user)
    if env is None:
        return HttpResponseBadRequest("Invalid or unknown rd")
    app_slug = provider_common.resolve_owned_app_slug(
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


@login_required
def integrations_user_google_callback(request: HttpRequest) -> HttpResponse:
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

    return redirect(provider_common.append_query(url=rd, extra={"connected": "google"}))


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one (env, owner, app).

    Returns a `provider_common` outcome dict where `outcome` is
    `has_token | absent | transient`. The `absent` (disconnected) path is
    intentionally silent — the broker asks every Refresh-all/bootstrap, and
    most providers are typically disconnected, so logging that as an error
    would be log spam.
    """
    try:
        google_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
    except IntegrationConfig.DoesNotExist:
        logger.error("google token refresh failed: IntegrationConfig(provider=google) missing")
        return provider_common.transient()

    def build_secrets(access_token: str, response: dict) -> provider_common.RefreshSecrets:
        return provider_common.RefreshSecrets(
            secrets={"access_token": access_token},
            expires_in=int(response.get("expires_in", 0)),
            row_metadata={},
        )

    return provider_common.run_refresh_exchange(
        provider=IntegrationUserCredential.Provider.GOOGLE,
        logger=logger,
        environment=environment,
        owner_user=owner_user,
        app_slug=app_slug,
        exchange=lambda refresh_token: _exchange_refresh_token(web=google_cfg.config, refresh_token=refresh_token),
        build_secrets=build_secrets,
    )


def _exchange_refresh_token(web: dict, refresh_token: str) -> provider_common.ExchangeResult:
    """POST to Google's token endpoint with grant_type=refresh_token.

    Google's refresh-token rejection returns 400 `invalid_grant`; treat that as
    revoked (the stored token is unusable, caller deletes the row). Any other
    non-200 / network / non-JSON failure is transient.
    """
    return provider_common.exchange_refresh_token(
        send=lambda: httpx.post(
            web["token_uri"],
            data={
                "client_id": web["client_id"],
                "client_secret": web["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=GOOGLE_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
        ),
        revoking_statuses=frozenset({400}),
        revoking_error_codes=frozenset({"invalid_grant"}),
        error_code_of=lambda body: str(body.get("error") or ""),
    )


def revoke(refresh_token: str) -> None:
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
