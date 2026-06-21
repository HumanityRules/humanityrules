"""Central env-SSO endpoints. The control plane owns the OAuth dance for every customer env.

- ``GET /auth/env-start?rd=<url>`` — entry point. The policy-proxy sidecar
  in the env redirects here when it sees an unauthenticated request. We
  resolve which env (and therefore which provider) ``rd`` belongs to,
  start the IdP dance, and remember ``rd`` + ``env_slug`` in a signed
  state JWT (no server-side session).
- ``GET /auth/env-callback?code=...&state=...`` — IdP redirect target.
  We exchange the code with the IdP, extract identity, mint a session
  JWT signed with the central RS256 key, and 302 the browser back to
  the env at ``<rd-host>/__humr_session_install?token=...&rd=...``.
- ``GET /.well-known/jwks.json`` — public-key publication. Per-env
  sidecars cache this JWKS and verify session JWTs locally.

The session JWT carries ``aud=<env_domain>`` (the env's globally unique
DNS zone) so a token issued for env A cannot be replayed against env B's
install endpoint. ``env.slug`` is only unique per AWS account, so it
cannot be used as a global audience under a single central JWKS.
"""

import base64
import logging
import secrets
import time
from urllib.parse import quote, urlencode, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from workos import WorkOSClient

from ..models import Environment, Organization

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "RS256"
STATE_TTL_SECONDS = 10 * 60
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60

PROVIDER_OIDC = "oidc"
PROVIDER_WORKOS = "workos"

ENV_CALLBACK_PATH = "/auth/env-callback"


def _control_plane_base_url(request: HttpRequest) -> str:
    scheme = "https" if request.is_secure() else "http"
    return f"{scheme}://{request.get_host()}"


def _b64url_uint(n: int) -> str:
    """RFC 7518 base64url-uint encoding for JWK n/e fields."""
    length = (n.bit_length() + 7) // 8
    raw = n.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwk_from_pem(public_pem: bytes, kid: str) -> dict:
    public_key = serialization.load_pem_public_key(public_pem)
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": JWT_ALGORITHM,
        "kid": kid,
        "n": _b64url_uint(n=numbers.n),
        "e": _b64url_uint(n=numbers.e),
    }


def _public_pem_from_private(private_pem: bytes) -> bytes:
    """Derive the public-key PEM on the fly. The private key is the source of truth."""
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _signing_key_pem() -> bytes:
    pem = settings.HUMR_ENV_SESSION_JWT_PRIVATE_KEY
    if not pem:
        raise RuntimeError("HUMR_ENV_SESSION_JWT_PRIVATE_KEY is not configured")
    return pem.encode("utf-8")


def _signing_kid() -> str:
    kid = settings.HUMR_ENV_SESSION_JWT_KID
    if not kid:
        raise RuntimeError("HUMR_ENV_SESSION_JWT_KID is not configured")
    return kid


def _resolve_env_for_rd(rd_url: str) -> Environment | None:
    """Match ``rd``'s host to an Environment by parent domain.

    Each env owns ``*.<env.shared_alb_hosted_zone>``; the rd must land on a
    subdomain of one of those. We scan envs (small set) and return the
    longest-suffix match. Returns None if rd is malformed or unowned.
    """
    parsed = urlparse(rd_url)
    if parsed.scheme != "https":
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None

    best: Environment | None = None
    best_zone_len = -1
    for env in Environment.objects.exclude(shared_alb_hosted_zone__isnull=True).exclude(shared_alb_hosted_zone=""):
        zone = (env.shared_alb_hosted_zone or "").lower()
        if not zone:
            continue
        if host == zone or host.endswith("." + zone):
            if len(zone) > best_zone_len:
                best = env
                best_zone_len = len(zone)
    return best


def _mint_state_jwt(rd_url: str, env_slug: str, nonce: str) -> str:
    now = int(time.time())
    return jwt.encode(
        payload={
            "rd": rd_url,
            "env_slug": env_slug,
            "nonce": nonce,
            "iat": now,
            "exp": now + STATE_TTL_SECONDS,
        },
        key=_signing_key_pem(),
        algorithm=JWT_ALGORITHM,
        headers={"kid": _signing_kid()},
    )


def _verify_state_jwt(state: str) -> dict | None:
    try:
        return jwt.decode(
            state,
            key=_public_pem_from_private(private_pem=_signing_key_pem()),
            algorithms=[JWT_ALGORITHM],
            leeway=5,
        )
    except jwt.PyJWTError as exc:
        logger.error("env-sso state reject: %s", exc)
        return None


def _mint_session_jwt(sub: str, email: str, username: str, provider: str, env_domain: str) -> str:
    now = int(time.time())
    return jwt.encode(
        payload={
            "sub": sub,
            "email": email,
            "username": username,
            "provider": provider,
            "aud": env_domain,
            "iat": now,
            "exp": now + SESSION_TTL_SECONDS,
        },
        key=_signing_key_pem(),
        algorithm=JWT_ALGORITHM,
        headers={"kid": _signing_kid()},
    )


def _start_oidc(*, request: HttpRequest, env: Environment, org: Organization, state: str, redirect_uri: str) -> HttpResponse:
    if not (org.oidc_issuer_url and org.oidc_client_id):
        logger.error("env-sso oidc start: org=%s missing oidc config", org.slug)
        return HttpResponse(content="organization not configured for oidc", status=502)
    params = urlencode({
        "client_id": org.oidc_client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return redirect(f"{org.oidc_issuer_url}/v1/authorize?{params}")


def _start_workos(*, state: str, redirect_uri: str) -> HttpResponse:
    """Send the user through WorkOS AuthKit. AuthKit handles the IdP picker."""
    client = WorkOSClient(api_key=settings.WORKOS_API_KEY, client_id=settings.WORKOS_CLIENT_ID)
    authorization_url = client.user_management.get_authorization_url(
        provider="authkit",
        redirect_uri=redirect_uri,
        state=state,
    )
    return redirect(authorization_url)


def env_start(request: HttpRequest) -> HttpResponse:
    """Sidecar-initiated entry point. Validate rd, pick provider, redirect to IdP."""
    rd = request.GET.get("rd", "")
    if not rd:
        return HttpResponse(content="missing rd", status=400)
    env = _resolve_env_for_rd(rd_url=rd)
    if env is None:
        logger.error("env-sso start reject: rd=%s did not match any env", rd)
        return HttpResponse(content="invalid rd", status=400)

    org = env.aws_account.organization
    nonce = secrets.token_urlsafe(16)
    state = _mint_state_jwt(rd_url=rd, env_slug=env.slug, nonce=nonce)
    redirect_uri = f"{_control_plane_base_url(request=request)}{ENV_CALLBACK_PATH}"

    if org.auth_provider == Organization.AuthProvider.OIDC:
        return _start_oidc(request=request, env=env, org=org, state=state, redirect_uri=redirect_uri)
    return _start_workos(state=state, redirect_uri=redirect_uri)


def _exchange_oidc_code(*, code: str, redirect_uri: str, org: Organization) -> dict:
    token_response = httpx.post(
        url=f"{org.oidc_issuer_url}/v1/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": org.oidc_client_id,
            "client_secret": org.oidc_client_secret,
        },
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
    )
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]
    userinfo = httpx.get(
        url=f"{org.oidc_issuer_url}/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
    )
    userinfo.raise_for_status()
    return userinfo.json()


def env_callback(request: HttpRequest) -> HttpResponse:
    """IdP redirect target. Mint session JWT and bounce to the env's install endpoint."""
    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    if not code or not state:
        return HttpResponse(content="missing code or state", status=400)
    claims = _verify_state_jwt(state=state)
    if claims is None:
        return HttpResponse(content="invalid or expired state", status=400)
    rd = claims.get("rd", "")
    env_slug = claims.get("env_slug", "")
    if not isinstance(rd, str) or not isinstance(env_slug, str):
        return HttpResponse(content="malformed state", status=400)

    env = _resolve_env_for_rd(rd_url=rd)
    if env is None or env.slug != env_slug:
        logger.error("env-sso callback reject: rd=%s env_slug=%s no match", rd, env_slug)
        return HttpResponse(content="invalid rd for env", status=400)

    org = env.aws_account.organization
    redirect_uri = f"{_control_plane_base_url(request=request)}{ENV_CALLBACK_PATH}"

    env_domain = env.shared_alb_hosted_zone
    if not env_domain:
        logger.error("env-sso callback reject: env=%s has no shared_alb_hosted_zone", env.slug)
        return HttpResponse(content="env not configured for sso", status=502)

    try:
        if org.auth_provider == Organization.AuthProvider.OIDC:
            userinfo = _exchange_oidc_code(code=code, redirect_uri=redirect_uri, org=org)
            sub = userinfo["sub"]
            email = userinfo.get("email", "")
            session_jwt = _mint_session_jwt(
                sub=sub, email=email, username=email, provider=PROVIDER_OIDC, env_domain=env_domain,
            )
        else:
            client = WorkOSClient(api_key=settings.WORKOS_API_KEY, client_id=settings.WORKOS_CLIENT_ID)
            auth_response = client.user_management.authenticate_with_code(code=code)
            workos_user = auth_response.user
            email = workos_user.email or ""
            session_jwt = _mint_session_jwt(
                sub=workos_user.id, email=email, username=email,
                provider=PROVIDER_WORKOS, env_domain=env_domain,
            )
    except Exception as exc:
        logger.error("env-sso code exchange failed env=%s provider=%s: %s", env.slug, org.auth_provider, exc)
        return HttpResponse(content="auth code exchange failed", status=502)

    rd_host = urlparse(rd).hostname or ""
    install_url = (
        f"https://{rd_host}/__humr_session_install?"
        f"token={quote(session_jwt, safe='')}&rd={quote(rd, safe='')}"
    )
    logger.info("env-sso callback ok env=%s provider=%s -> %s", env.slug, org.auth_provider, rd)
    return redirect(install_url)


def env_jwks(request: HttpRequest) -> JsonResponse:
    """Public JWKS for sidecar verification. Cached at the edge for 5 minutes."""
    public_pem = _public_pem_from_private(private_pem=_signing_key_pem())
    body = {"keys": [_jwk_from_pem(public_pem=public_pem, kid=_signing_kid())]}
    response = JsonResponse(data=body)
    response["Cache-Control"] = "public, max-age=300"
    return response
