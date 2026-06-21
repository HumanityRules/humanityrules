"""GitHub per-user integration: OAuth connect + token refresh (runtime-scoped).

Distinct from `org_github.py`, which handles the org-admin GitHub App
*installation* flow used by the HUMR control plane to enumerate repos. This
file is the per-user OAuth dance: an end user inside a Hermes WebUI clicks
"Connect GitHub", consents at github.com, and a refresh_token is persisted
on HUMR as an IntegrationUserCredential row.

We reuse the existing GitHub App's `client_id`/`client_secret` because a
GitHub App can issue user-to-server tokens via the same OAuth endpoints. The
"Expire user authorization tokens" toggle on the App must be ON, so each
user authorization yields an 8h access_token + 6mo refresh_token.

User-to-server tokens act as the authorizing user (commits attribute to
them), but a token's effective access on a repo is gated by whether the App
is installed on the owning account/org. GitHub stitches authorize+install
into one flow when the user authorizes from an account that hasn't
installed the App — no separate install step required from the WebUI.

Refresh (`refresh_outcome`) mirrors `provider_google`: it exchanges the
stored refresh_token and returns a broker-shaped outcome dict for the batched
endpoint. GitHub rotates the refresh_token on every successful refresh — we
always persist the new one. A 6-month idle window invalidates the refresh
(GitHub returns 200 with `error=bad_refresh_token` or similar); we delete the
row and the outcome flips to `absent` so the WebUI prompts a reconnect.
"""

import logging
import secrets
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils import timezone

from humanityrules_app.models import Environment, IntegrationUserCredential, User
from humanityrules_app.views.integrations import provider_common


logger = logging.getLogger(__name__)


GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 30
# GitHub's refresh-token exchange is a single small POST. Healthy P99 is
# well under 1s; setting the ceiling at 5s means the broker's batch refresh
# (which runs all providers in parallel) is naturally bounded by the slowest
# single exchange, no separate batch deadline needed. If upstream is taking
# longer than 5s, surfacing `transient` (cache-preserving) beats hanging a
# user request on a refresh that's about to fail anyway.
GITHUB_TOKEN_REFRESH_TIMEOUT_SECONDS = 5

# GitHub returns an HTTP 200 with an error JSON body when the refresh_token
# is no longer valid. These are the codes that indicate a permanent failure
# and should trigger row deletion (the outcome flips to `absent` so the
# UI prompts a reconnect), vs a transient error that should be retried.
_REVOKED_ERROR_CODES = frozenset({
    "bad_refresh_token",
    "bad_credentials",
    "unauthorized_client",
    "invalid_grant",
})


def _redirect_uri(request: HttpRequest) -> str:
    """Build the absolute callback URL for this host.

    GitHub validates `redirect_uri` against the App's Callback URL list.
    Each customer-facing HUMR host where users may connect must be listed
    on the GitHub App's settings page.
    """
    return request.build_absolute_uri("/integrations/user/github/callback/")


@login_required
def integrations_user_github_start(request: HttpRequest) -> HttpResponse:
    """Validate `rd`, stash state, redirect to GitHub's OAuth consent screen."""
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


@login_required
def integrations_user_github_callback(request: HttpRequest) -> HttpResponse:
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
            "GitHub did not return a refresh token. The HUMR GitHub App must have "
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

    return redirect(provider_common.append_query(url=rd, extra={"connected": "github"}))


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one (env, owner, app).

    Mirror of `provider_google.refresh_outcome`. CAS-delete-on-revoked and
    CAS-update-on-rotate semantics are preserved (see inline comments). A
    lost CAS race surfaces as `transient`, just like a network blip: the
    next refresh sees the winner's rotated R2.
    """
    if not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        logger.error("github token refresh failed: GITHUB_APP_CLIENT_ID/SECRET not configured")
        return provider_common.transient()

    integration = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.GITHUB,
    ).first()
    if integration is None:
        return provider_common.absent()

    old_refresh = integration.credentials.get("refresh_token", "")
    if not old_refresh:
        logger.error(
            "github token refresh: row missing refresh_token env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        return provider_common.absent()
    exchange_result = _exchange_refresh_token(refresh_token=old_refresh)

    # Whatever happens next, we must only mutate the row if its
    # refresh_token is still old_refresh. Two callers can read the same
    # R1, race at GitHub's token endpoint, and disagree on what the row
    # should look like — but only one of them was actually authoritative
    # (the one whose R1 the row still holds at decision time).

    if exchange_result.revoked:
        # Compare-and-swap delete: GitHub said R1 is dead, but if the row
        # has since rotated to R2 (a concurrent winner), R1 being dead is
        # expected — the row is fine. Don't delete a valid grant.
        deleted, _ = IntegrationUserCredential.objects.filter(
            id=integration.id, credentials__refresh_token=old_refresh,
        ).delete()
        if deleted:
            logger.info(
                "github token refresh: revoked by github, deleted row env=%s owner=%s app=%s",
                environment.slug, owner_user.username, app_slug,
            )
            return provider_common.absent()
        # Row already rotated by a concurrent refresh — surface as
        # transient so we don't overwrite the winner's cache; the next
        # call reads the winner's R2.
        logger.info(
            "github token refresh: stale revoke (row rotated under us) env=%s owner=%s app=%s",
            environment.slug, owner_user.username, app_slug,
        )
        return provider_common.transient()

    if exchange_result.error is not None:
        logger.error(
            "github token refresh failed env=%s owner=%s app=%s error=%s",
            environment.slug, owner_user.username, app_slug, exchange_result.error,
        )
        return provider_common.transient()

    # Compare-and-swap update: only rotate if the row still holds R1. A
    # peer who also got back a successful rotation may have already
    # written R2 — in that case our new_refresh would be a now-orphan
    # value. Affected_rows == 0 just means we lost; our access_token
    # is still valid for ~8h, so we return it without persisting.
    new_refresh = exchange_result.response.get("refresh_token", "") or old_refresh
    refreshed_at = provider_common.now()
    IntegrationUserCredential.objects.filter(
        id=integration.id, credentials__refresh_token=old_refresh,
    ).update(
        credentials={**integration.credentials, "refresh_token": new_refresh},
        last_refreshed_at=refreshed_at,
        updated_at=refreshed_at,
    )

    return provider_common.has_token(
        secrets={"access_token": exchange_result.response["access_token"]},
        expires_in=int(exchange_result.response.get("expires_in", 0)),
        config={},
        metadata={},
    )


def _exchange_refresh_token(refresh_token: str) -> provider_common.ExchangeResult:
    """POST to GitHub's token endpoint with grant_type=refresh_token.

    GitHub's quirk: failed refreshes return HTTP 200 with an error body, not
    a 4xx. Classification keys off the JSON `error` field, not status code.
    """
    try:
        response = httpx.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_APP_CLIENT_ID,
                "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=GITHUB_TOKEN_REFRESH_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return provider_common.ExchangeResult(response=None, revoked=False, error=f"network: {exc}")

    if response.status_code != 200:
        return provider_common.ExchangeResult(
            response=None, revoked=False,
            error=f"http {response.status_code}",
        )

    try:
        body = response.json()
    except ValueError:
        return provider_common.ExchangeResult(response=None, revoked=False, error="non-json-200")

    if "error" in body:
        if body["error"] in _REVOKED_ERROR_CODES:
            return provider_common.ExchangeResult(response=None, revoked=True, error=None)
        return provider_common.ExchangeResult(
            response=None, revoked=False,
            error=f"github error: {body['error']}",
        )

    if "access_token" not in body:
        return provider_common.ExchangeResult(
            response=None, revoked=False,
            error="missing access_token in response",
        )

    return provider_common.ExchangeResult(response=body, revoked=False, error=None)


def revoke(refresh_token: str) -> None:
    """Best-effort revoke at GitHub's grants endpoint.

    Failure doesn't block the local deletion — the row removal is the
    load-bearing step.
    """
    if not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        return
    try:
        # httpx.delete() has no json= param; GitHub's grant endpoint needs a JSON body.
        httpx.request(
            "DELETE",
            f"{GITHUB_API_BASE}/applications/{settings.GITHUB_APP_CLIENT_ID}/grant",
            auth=(settings.GITHUB_APP_CLIENT_ID, settings.GITHUB_APP_CLIENT_SECRET),
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            json={"access_token": refresh_token},
            timeout=10,
        )
    except Exception as exc:
        logger.error("github revoke best-effort failed: %s", exc)
