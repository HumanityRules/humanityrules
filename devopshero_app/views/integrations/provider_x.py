"""X (Twitter) per-user integration: OAuth 2.0 connect + token refresh.

A near-clone of `provider_google`: the authenticated DOH user starts at
`/integrations/user/x/start/?rd=<URL>`, consents at X, and lands back at
`/integrations/user/x/callback/`, which persists the refresh_token as an
IntegrationUserCredential row. The broker swaps a placeholder Bearer for a
fresh access token on `api.x.com`; no long-lived X credential enters the env.

Two things differ from Google (both X requirements, verified against
docs.x.com), so this can't just reuse `provider_google`:

1. **PKCE is mandatory.** `/authorize` carries `code_challenge` +
   `code_challenge_method=S256`; the token exchange carries the matching
   `code_verifier`. The verifier is stashed in the session between the two
   browser hops.
2. **Confidential-client auth is HTTP Basic.** The token and refresh exchanges
   authenticate with `Authorization: Basic base64(client_id:client_secret)` and
   omit `client_id` from the body — where Google puts `client_secret` in the body.

X access tokens are opaque (not JWTs), so expiry comes from the response's
`expires_in` (2h), not a token claim.

See `docs/integrations/integrations_broker_design.md`.
"""

import base64
import hashlib
import logging
import secrets
from urllib.parse import urlencode, urlparse

import httpx
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils import timezone

from devopshero_app.models import Environment, IntegrationConfig, IntegrationUserCredential, User
from devopshero_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


# Scopes covering the xurl skill's surface (post/read/search/engage/follow/DM).
# `offline.access` is what makes X return a refresh_token; without it we'd get
# only a 2h access token and no way to renew it for env-resident callers.
X_SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "follows.read",
    "follows.write",
    "like.read",
    "like.write",
    "bookmark.read",
    "bookmark.write",
    "list.read",
    "list.write",
    "mute.read",
    "mute.write",
    "block.read",
    "block.write",
    "dm.read",
    "dm.write",
    "media.write",
    "offline.access",
]

# X's refresh exchange is a single small POST; see the rationale on Google's
# equivalent constant. 5s ceiling keeps the broker's parallel batch bounded.
X_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5

# X confidential-client revoke endpoint (best-effort on disconnect).
X_REVOKE_URI = "https://api.x.com/2/oauth2/revoke"


def _pick_redirect_uri(request: HttpRequest, configured: list[str]) -> str:
    """Return the registered redirect URI whose host matches *request*'s host.

    X validates `redirect_uri` exactly — byte-identical to a registered URI AND
    byte-identical between /authorize and token exchange. Both browser hops hit
    the same host, so recompute the choice from request.get_host() each step.
    """
    request_host = request.get_host().lower()
    for uri in configured:
        if urlparse(uri).netloc.lower() == request_host:
            return uri
    raise ValueError(
        f"No redirect_uri configured for host {request_host!r}. Available: {configured!r}"
    )


def _basic_auth_header(web: dict) -> str:
    """Build the `Basic base64(client_id:client_secret)` header value for X."""
    raw = f"{web['client_id']}:{web['client_secret']}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _pkce_challenge(verifier: str) -> str:
    """Derive the S256 PKCE code_challenge from *verifier* (base64url, no padding)."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@login_required
def integrations_user_x_start(request: HttpRequest) -> HttpResponse:
    """Validate `rd`, stash state + PKCE verifier, redirect to X's consent screen."""
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
        x_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.X)
    except IntegrationConfig.DoesNotExist:
        logger.error("x integration start failed: IntegrationConfig(provider=x) missing")
        return HttpResponseBadRequest(
            "X integration not configured. Run: uv run manage.py setup_x_oauth_client ..."
        )

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    request.session["x_oauth_state"] = state
    request.session["x_oauth_code_verifier"] = code_verifier
    request.session["x_oauth_payload"] = {
        "rd": rd,
        "env_id": str(env.id),
        "app_slug": app_slug,
        "owner_username": request.user.username,
    }

    web = x_cfg.config
    try:
        redirect_uri = _pick_redirect_uri(request=request, configured=web["redirect_uris"])
    except ValueError as exc:
        logger.error("x oauth start failed: %s", exc)
        return HttpResponseBadRequest("X OAuth client has no redirect_uri registered for this host.")

    params = urlencode({
        "client_id": web["client_id"],
        "response_type": "code",
        "scope": " ".join(X_SCOPES),
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": _pkce_challenge(verifier=code_verifier),
        "code_challenge_method": "S256",
    })
    return redirect(f"{web['auth_uri']}?{params}")


def _exchange_x_code(web: dict, code: str, redirect_uri: str, code_verifier: str) -> dict:
    """POST to X's token endpoint (confidential client: Basic auth, no client_id in body)."""
    response = httpx.post(
        web["token_uri"],
        headers={"Authorization": _basic_auth_header(web=web)},
        data={
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


@login_required
def integrations_user_x_callback(request: HttpRequest) -> HttpResponse:
    """Exchange X's auth code, persist refresh_token on DOH, 302 back to `rd`."""
    if request.GET.get("error"):
        logger.error("x oauth callback error=%s", request.GET.get("error"))
        return HttpResponseBadRequest(f"X OAuth error: {request.GET['error']}")

    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    if not code or not state:
        return HttpResponseBadRequest("Missing code or state")

    expected_state = request.session.pop("x_oauth_state", "")
    code_verifier = request.session.pop("x_oauth_code_verifier", "")
    payload = request.session.pop("x_oauth_payload", None)
    if not expected_state or state != expected_state or not isinstance(payload, dict):
        return HttpResponseBadRequest("Invalid state")
    if not code_verifier:
        return HttpResponseBadRequest("Missing PKCE verifier")

    rd = payload.get("rd", "")
    env_id = payload.get("env_id", "")
    app_slug = payload.get("app_slug", "")
    owner_username = payload.get("owner_username", "")
    if not rd or not env_id or not app_slug or not owner_username:
        return HttpResponseBadRequest("Corrupt session payload")

    # Defense in depth: the authenticated user must own the session payload.
    if owner_username != request.user.username:
        logger.error(
            "x callback user mismatch session_user=%s request_user=%s",
            owner_username, request.user.username,
        )
        return HttpResponseBadRequest("User mismatch")

    try:
        x_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.X)
    except IntegrationConfig.DoesNotExist:
        logger.error("x callback failed: IntegrationConfig(provider=x) missing")
        return HttpResponseBadRequest("X integration not configured")

    try:
        env = Environment.objects.get(id=env_id)
    except Environment.DoesNotExist:
        logger.error("x callback failed: env_id=%s not found", env_id)
        return HttpResponseBadRequest("Environment not found")

    try:
        redirect_uri = _pick_redirect_uri(request=request, configured=x_cfg.config["redirect_uris"])
    except ValueError as exc:
        logger.error("x callback failed: %s", exc)
        return HttpResponseBadRequest("X OAuth client has no redirect_uri registered for this host.")

    try:
        token_response = _exchange_x_code(
            web=x_cfg.config, code=code, redirect_uri=redirect_uri, code_verifier=code_verifier,
        )
    except Exception as exc:
        logger.error("x token exchange failed: %s", exc)
        return HttpResponseBadRequest("X token exchange failed")

    refresh_token = token_response.get("refresh_token", "")
    if not refresh_token:
        logger.error(
            "x token exchange returned no refresh_token env=%s user=%s (missing offline.access scope?)",
            env.slug, owner_username,
        )
        return HttpResponseBadRequest(
            "X did not return a refresh_token. Ensure the offline.access scope is granted and reconnect."
        )

    IntegrationUserCredential.objects.update_or_create(
        owner_user=request.user,
        environment=env,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.X,
        defaults={
            "credentials": {"refresh_token": refresh_token},
            "config": {"scope": token_response.get("scope", "")},
            "metadata": {"connected_at": timezone.now().isoformat()},
            "last_refreshed_at": None,
        },
    )
    logger.info("x integration stored env=%s owner=%s app=%s", env.slug, owner_username, app_slug)

    return redirect(provider_common.append_query(url=rd, extra={"connected": "x"}))


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one (env, owner, app)."""
    try:
        x_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.X)
    except IntegrationConfig.DoesNotExist:
        logger.error("x token refresh failed: IntegrationConfig(provider=x) missing")
        return provider_common.transient()

    def build_secrets(access_token: str, response: dict) -> provider_common.RefreshSecrets:
        return provider_common.RefreshSecrets(
            secrets={"access_token": access_token},
            expires_in=int(response.get("expires_in", 0)),
            row_metadata={},
        )

    return provider_common.run_refresh_exchange(
        provider=IntegrationUserCredential.Provider.X,
        logger=logger,
        environment=environment,
        owner_user=owner_user,
        app_slug=app_slug,
        exchange=lambda refresh_token: _exchange_refresh_token(web=x_cfg.config, refresh_token=refresh_token),
        build_secrets=build_secrets,
    )


def _exchange_refresh_token(web: dict, refresh_token: str) -> provider_common.ExchangeResult:
    """POST to X's token endpoint with grant_type=refresh_token (Basic auth).

    X rejects a dead refresh_token with 400 `invalid_request` (or
    `invalid_grant`); treat both as revoked so the caller deletes the row.
    X rotates refresh tokens on each refresh — the response carries a new one,
    which `run_refresh_exchange` persists.
    """
    return provider_common.exchange_refresh_token(
        send=lambda: httpx.post(
            web["token_uri"],
            headers={"Authorization": _basic_auth_header(web=web)},
            data={
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=X_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
        ),
        revoking_statuses=frozenset({400}),
        revoking_error_codes=frozenset({"invalid_request", "invalid_grant"}),
        error_code_of=lambda body: str(body.get("error") or ""),
    )


def revoke(refresh_token: str) -> None:
    """Best-effort revoke at X's oauth2 revoke endpoint (Basic auth).

    Failure doesn't block local deletion — the row removal is load-bearing.
    """
    try:
        x_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.X)
    except IntegrationConfig.DoesNotExist:
        logger.error("x revoke skipped: IntegrationConfig(provider=x) missing")
        return
    try:
        httpx.post(
            X_REVOKE_URI,
            headers={"Authorization": _basic_auth_header(web=x_cfg.config)},
            data={"token": refresh_token, "token_type_hint": "refresh_token"},
            timeout=10,
        )
    except Exception as exc:
        logger.error("x revoke best-effort failed: %s", exc)
