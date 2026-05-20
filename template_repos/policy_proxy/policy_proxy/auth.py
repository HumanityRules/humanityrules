"""Auth-service routes: OAuth dance + JWT minting + JWKS endpoint.

Runs as a singleton per env, fronted by the env's shared ALB via a host-based
listener rule on auth.<env-domain>. The provider is selected at secret-load
time:

- ``provider == "oidc"`` runs the standard OIDC code flow against the
  Organization's per-org issuer (Okta today, but no Okta-specific quirks).
- ``provider == "workos"`` runs the WorkOS social-login dance against the
  shared DOH WorkOS app, using the GoogleOAuth provider connection.

The session JWT minted from either branch carries a ``provider`` claim so the
PDP can pick the right column on the User row (``oidc_sub`` vs
``workos_user_id``).

Routes:
- GET /start?rd=<url>               begin the OAuth dance
- GET /callback?code=...&state=...  exchange code + mint session JWT
- GET /.well-known/jwks.json        public key for policy-proxy verification
"""

import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

import boto3
import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from . import config as config_mod

logger = logging.getLogger(__name__)

# RS256 for JWKS interoperability with the sidecar's PyJWKClient.
JWT_ALGORITHM = "RS256"
# OAuth state param must be consumed within 10 minutes.
STATE_TTL_SECONDS = 10 * 60
WORKOS_PKCE_COOKIE_NAME = "doh_workos_pkce"
WORKOS_PKCE_COOKIE_PATH = "/callback"
WORKOS_PKCE_PURPOSE = "workos_pkce"
WORKOS_PKCE_CHALLENGE_METHOD = "S256"

# Provider tag values stored in the secret + asserted in the session JWT.
PROVIDER_OIDC = "oidc"
PROVIDER_WORKOS = "workos"
ProviderName = Literal["oidc", "workos"]

# WorkOS hosted authorize / token endpoints. The WorkOS API base is shared
# across all DOH-managed personal orgs (single WorkOS app, many users).
WORKOS_API_BASE = "https://api.workos.com"
WORKOS_AUTHORIZE_PATH = "/user_management/authorize"
WORKOS_AUTHENTICATE_PATH = "/user_management/authenticate"
# Provider value that triggers WorkOS's GoogleOAuth social login flow.
WORKOS_GOOGLE_PROVIDER = "GoogleOAuth"


@dataclass(frozen=True)
class OidcConfig:
    issuer_url: str
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class WorkOSConfig:
    client_id: str


@dataclass(frozen=True)
class JwtKeyConfig:
    private_pem: bytes
    public_pem: bytes
    kid: str


@dataclass(frozen=True)
class AuthRuntimeConfig:
    """Materialized config loaded from Secrets Manager at startup.

    Exactly one of ``oidc`` or ``workos`` is populated, matching ``provider``.
    """
    provider: ProviderName
    oidc: OidcConfig | None
    workos: WorkOSConfig | None
    jwt_key: JwtKeyConfig


@dataclass(frozen=True)
class StateClaims:
    rd: str
    nonce: str


def load_runtime_config(secret_arn: str, secrets_client: Any) -> AuthRuntimeConfig:
    """Fetch the auth-config secret and unpack it into a provider bundle + JWT key."""
    response = secrets_client.get_secret_value(SecretId=secret_arn)
    data = json.loads(response["SecretString"])
    provider = data["provider"]
    jwt_data = data["jwt_key"]
    jwt_key = JwtKeyConfig(
        private_pem=jwt_data["private_pem"].encode("utf-8"),
        public_pem=jwt_data["public_pem"].encode("utf-8"),
        kid=jwt_data["kid"],
    )
    if provider == PROVIDER_OIDC:
        oidc_data = data["oidc_config"]
        return AuthRuntimeConfig(
            provider=PROVIDER_OIDC,
            oidc=OidcConfig(
                issuer_url=oidc_data["issuer_url"].rstrip("/"),
                client_id=oidc_data["client_id"],
                client_secret=oidc_data["client_secret"],
            ),
            workos=None,
            jwt_key=jwt_key,
        )
    if provider == PROVIDER_WORKOS:
        workos_data = data["workos_config"]
        return AuthRuntimeConfig(
            provider=PROVIDER_WORKOS,
            oidc=None,
            workos=WorkOSConfig(
                client_id=workos_data["client_id"],
            ),
            jwt_key=jwt_key,
        )
    raise RuntimeError(f"unknown auth provider in secret: {provider!r}")


def _validate_rd(rd_url: str, env_domain: str) -> bool:
    """Only accept redirects that stay within the env's parent domain."""
    parsed = urllib.parse.urlparse(rd_url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    parent = env_domain.lower()
    return host == parent or host.endswith("." + parent)


def _new_state_nonce() -> str:
    """Generate the state nonce used to bind state to provider-local state."""
    return secrets.token_urlsafe(16)


def _mint_state(rd_url: str, nonce: str, key: JwtKeyConfig) -> str:
    now = int(time.time())
    return jwt.encode(
        payload={
            "rd": rd_url,
            "iat": now,
            "exp": now + STATE_TTL_SECONDS,
            "nonce": nonce,
        },
        key=key.private_pem,
        algorithm=JWT_ALGORITHM,
        headers={"kid": key.kid},
    )


def _verify_state(state: str, key: JwtKeyConfig) -> StateClaims | None:
    """Verify the state JWT and return the embedded claims, or None on failure."""
    try:
        claims = jwt.decode(
            state, key=key.public_pem, algorithms=[JWT_ALGORITHM], leeway=5,
        )
    except jwt.PyJWTError as exc:
        logger.error("state reject: %s", exc)
        return None
    rd = claims.get("rd")
    nonce = claims.get("nonce")
    if not (isinstance(rd, str) and isinstance(nonce, str)):
        logger.error("state reject reason=missing-claim")
        return None
    return StateClaims(rd=rd, nonce=nonce)


def _new_workos_code_verifier() -> str:
    """Generate a PKCE verifier in the RFC 7636 verifier character set."""
    return secrets.token_urlsafe(64)


def _workos_code_challenge(code_verifier: str) -> str:
    """Derive the S256 PKCE code challenge for WorkOS authorize."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _mint_workos_pkce_cookie(nonce: str, code_verifier: str, key: JwtKeyConfig) -> str:
    """Sign the WorkOS PKCE verifier for the auth-host callback cookie."""
    now = int(time.time())
    return jwt.encode(
        payload={
            "purpose": WORKOS_PKCE_PURPOSE,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "iat": now,
            "exp": now + STATE_TTL_SECONDS,
        },
        key=key.private_pem,
        algorithm=JWT_ALGORITHM,
        headers={"kid": key.kid},
    )


def _verify_workos_pkce_cookie(cookie_value: str, nonce: str, key: JwtKeyConfig) -> str | None:
    """Return the PKCE verifier when the signed cookie matches the state nonce."""
    try:
        claims = jwt.decode(
            cookie_value, key=key.public_pem, algorithms=[JWT_ALGORITHM], leeway=5,
        )
    except jwt.PyJWTError as exc:
        logger.error("workos pkce reject: %s", exc)
        return None

    if claims.get("purpose") != WORKOS_PKCE_PURPOSE or claims.get("nonce") != nonce:
        logger.error("workos pkce reject reason=nonce-or-purpose-mismatch")
        return None
    code_verifier = claims.get("code_verifier")
    if not isinstance(code_verifier, str):
        logger.error("workos pkce reject reason=missing-code-verifier")
        return None
    return code_verifier


def _set_workos_pkce_cookie(response: Response, cookie_value: str) -> None:
    """Attach the short-lived PKCE verifier cookie to the WorkOS start redirect."""
    response.set_cookie(
        key=WORKOS_PKCE_COOKIE_NAME,
        value=cookie_value,
        max_age=STATE_TTL_SECONDS,
        path=WORKOS_PKCE_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _clear_workos_pkce_cookie(response: Response) -> None:
    """Clear the one-use WorkOS PKCE verifier cookie after callback handling."""
    response.delete_cookie(
        key=WORKOS_PKCE_COOKIE_NAME,
        path=WORKOS_PKCE_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _mint_session_jwt(
    sub: str,
    username: str,
    email: str,
    provider: ProviderName,
    ttl_seconds: int,
    key: JwtKeyConfig,
) -> str:
    now = int(time.time())
    return jwt.encode(
        payload={
            "sub": sub,
            "username": username,
            "email": email,
            "provider": provider,
            "iat": now,
            "exp": now + ttl_seconds,
        },
        key=key.private_pem,
        algorithm=JWT_ALGORITHM,
        headers={"kid": key.kid},
    )


def _session_cookie(jwt_value: str, env_domain: str, ttl_seconds: int) -> str:
    return (
        f"doh_session={jwt_value}; Domain=.{env_domain}; Path=/; "
        f"Max-Age={ttl_seconds}; Secure; HttpOnly; SameSite=Lax"
    )


def _b64url_uint(n: int) -> str:
    # Smallest big-endian byte representation, base64url-encoded, no padding.
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
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }


@dataclass(frozen=True)
class CallbackIdentity:
    """Provider-agnostic shape returned by the code-exchange helpers."""
    sub: str
    email: str
    username: str


async def _exchange_oidc_code(
    code: str,
    redirect_uri: str,
    oidc: OidcConfig,
    http_client: httpx.AsyncClient,
) -> CallbackIdentity:
    token_response = await http_client.post(
        url=f"{oidc.issuer_url}/v1/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": oidc.client_id,
            "client_secret": oidc.client_secret,
        },
    )
    if token_response.status_code != 200:
        raise RuntimeError(
            f"oidc token exchange failed status={token_response.status_code} "
            f"body={token_response.text[:200]!r}",
        )
    access_token = token_response.json()["access_token"]

    userinfo_response = await http_client.get(
        url=f"{oidc.issuer_url}/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_response.status_code != 200:
        raise RuntimeError(
            f"oidc userinfo failed status={userinfo_response.status_code} "
            f"body={userinfo_response.text[:200]!r}",
        )
    userinfo = userinfo_response.json()
    email = userinfo.get("email", "")
    return CallbackIdentity(sub=userinfo["sub"], email=email, username=email)


async def _exchange_workos_code(
    code: str,
    code_verifier: str,
    workos: WorkOSConfig,
    http_client: httpx.AsyncClient,
) -> CallbackIdentity:
    """Single round-trip authenticate-with-code call against WorkOS."""
    response = await http_client.post(
        url=f"{WORKOS_API_BASE}{WORKOS_AUTHENTICATE_PATH}",
        json={
            "client_id": workos.client_id,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "code": code,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"workos authenticate failed status={response.status_code} "
            f"body={response.text[:200]!r}",
        )
    user = response.json()["user"]
    email = user.get("email", "")
    return CallbackIdentity(sub=user["id"], email=email, username=email)


def build_auth_router(cfg: config_mod.AuthServiceConfig) -> APIRouter:
    """Build the FastAPI router for the auth-service role. Caller mounts runtime on app.state."""
    router = APIRouter()

    @router.get("/start")
    async def start(request: Request) -> Response:
        rd = request.query_params.get("rd", "")
        if not rd or not _validate_rd(rd_url=rd, env_domain=cfg.env_domain):
            return PlainTextResponse(content="invalid rd parameter", status_code=400)

        runtime: AuthRuntimeConfig = request.app.state.auth_runtime
        nonce = _new_state_nonce()
        state = _mint_state(rd_url=rd, nonce=nonce, key=runtime.jwt_key)
        redirect_uri = f"{cfg.auth_base_url}/callback"
        if runtime.provider == PROVIDER_OIDC:
            assert runtime.oidc is not None
            authorize_url = (
                f"{runtime.oidc.issuer_url}/v1/authorize?"
                + urllib.parse.urlencode({
                    "client_id": runtime.oidc.client_id,
                    "response_type": "code",
                    "scope": "openid email profile",
                    "redirect_uri": redirect_uri,
                    "state": state,
                })
            )
            return RedirectResponse(url=authorize_url, status_code=302)

        assert runtime.workos is not None
        code_verifier = _new_workos_code_verifier()
        code_challenge = _workos_code_challenge(code_verifier=code_verifier)
        pkce_cookie = _mint_workos_pkce_cookie(nonce=nonce, code_verifier=code_verifier, key=runtime.jwt_key)
        authorize_url = (
            f"{WORKOS_API_BASE}{WORKOS_AUTHORIZE_PATH}?"
            + urllib.parse.urlencode({
                "client_id": runtime.workos.client_id,
                "response_type": "code",
                "provider": WORKOS_GOOGLE_PROVIDER,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": WORKOS_PKCE_CHALLENGE_METHOD,
            })
        )
        response = RedirectResponse(url=authorize_url, status_code=302)
        _set_workos_pkce_cookie(response=response, cookie_value=pkce_cookie)
        return response

    @router.get("/callback")
    async def callback(request: Request) -> Response:
        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        if not code or not state:
            return PlainTextResponse(content="missing code or state", status_code=400)

        runtime: AuthRuntimeConfig = request.app.state.auth_runtime
        state_claims = _verify_state(state=state, key=runtime.jwt_key)
        if state_claims is None or not _validate_rd(rd_url=state_claims.rd, env_domain=cfg.env_domain):
            return PlainTextResponse(content="invalid or expired state", status_code=400)

        try:
            if runtime.provider == PROVIDER_OIDC:
                assert runtime.oidc is not None
                identity = await _exchange_oidc_code(
                    code=code,
                    redirect_uri=f"{cfg.auth_base_url}/callback",
                    oidc=runtime.oidc,
                    http_client=request.app.state.http_client,
                )
            else:
                assert runtime.workos is not None
                pkce_cookie = request.cookies.get(WORKOS_PKCE_COOKIE_NAME)
                if not pkce_cookie:
                    return PlainTextResponse(content="missing pkce cookie", status_code=400)
                code_verifier = _verify_workos_pkce_cookie(
                    cookie_value=pkce_cookie, nonce=state_claims.nonce, key=runtime.jwt_key,
                )
                if code_verifier is None:
                    response = PlainTextResponse(content="invalid or expired pkce cookie", status_code=400)
                    _clear_workos_pkce_cookie(response=response)
                    return response
                identity = await _exchange_workos_code(
                    code=code,
                    code_verifier=code_verifier,
                    workos=runtime.workos,
                    http_client=request.app.state.http_client,
                )
        except Exception as exc:
            logger.error("auth code exchange failed provider=%s: %s", runtime.provider, exc)
            return PlainTextResponse(content="auth code exchange failed", status_code=502)

        session_jwt = _mint_session_jwt(
            sub=identity.sub,
            username=identity.username,
            email=identity.email,
            provider=runtime.provider,
            ttl_seconds=cfg.session_ttl_seconds,
            key=runtime.jwt_key,
        )
        response = Response(status_code=302, headers={"location": state_claims.rd})
        response.headers.append(
            "set-cookie",
            _session_cookie(
                jwt_value=session_jwt, env_domain=cfg.env_domain, ttl_seconds=cfg.session_ttl_seconds,
            ),
        )
        if runtime.provider == PROVIDER_WORKOS:
            _clear_workos_pkce_cookie(response=response)
        logger.info(
            "auth callback ok env=%s provider=%s sub=%s email=%s -> %s",
            cfg.env_domain, runtime.provider, identity.sub, identity.email, state_claims.rd,
        )
        return response

    @router.get("/.well-known/jwks.json")
    async def jwks(request: Request) -> Response:
        runtime: AuthRuntimeConfig = request.app.state.auth_runtime
        body = {"keys": [_jwk_from_pem(public_pem=runtime.jwt_key.public_pem, kid=runtime.jwt_key.kid)]}
        return JSONResponse(content=body, headers={"cache-control": "public, max-age=300"})

    return router


def install_auth_routes(
    app: FastAPI,
    cfg: config_mod.AuthServiceConfig,
    secrets_client: Any,
) -> None:
    """Attach runtime state (config + http client) and mount the auth router."""
    app.state.auth_runtime = load_runtime_config(
        secret_arn=cfg.auth_config_secret_arn, secrets_client=secrets_client,
    )
    # Shared async httpx client for Okta calls (token + userinfo).
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
    )
    app.include_router(build_auth_router(cfg=cfg))


def new_secrets_client() -> Any:
    """Create a boto3 Secrets Manager client; extracted so tests can monkeypatch."""
    return boto3.client("secretsmanager")
