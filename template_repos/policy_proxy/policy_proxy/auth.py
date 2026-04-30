"""Auth-service routes: Okta OAuth dance + JWT minting + JWKS endpoint.

Runs as a singleton per env, fronted by the env's shared ALB via a host-based
listener rule on auth.<env-domain>. Mirrors the former auth-Lambda handler
behavior; see docs/policy_proxy_design.md.

Routes:
- GET /start?rd=<url>               begin the OAuth dance
- GET /callback?code=...&state=...  exchange code + mint session JWT
- GET /.well-known/jwks.json        public key for policy-proxy verification
"""

import base64
import json
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class OidcConfig:
    issuer_url: str
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class JwtKeyConfig:
    private_pem: bytes
    public_pem: bytes
    kid: str


@dataclass(frozen=True)
class AuthRuntimeConfig:
    """Materialized config loaded from Secrets Manager at startup."""
    oidc: OidcConfig
    jwt_key: JwtKeyConfig


def load_runtime_config(secret_arn: str, secrets_client: Any) -> AuthRuntimeConfig:
    """Fetch the auth-config secret and unpack it into OIDC + JWT key bundles."""
    response = secrets_client.get_secret_value(SecretId=secret_arn)
    data = json.loads(response["SecretString"])
    oidc_data = data["oidc_config"]
    jwt_data = data["jwt_key"]
    return AuthRuntimeConfig(
        oidc=OidcConfig(
            issuer_url=oidc_data["issuer_url"].rstrip("/"),
            client_id=oidc_data["client_id"],
            client_secret=oidc_data["client_secret"],
        ),
        jwt_key=JwtKeyConfig(
            private_pem=jwt_data["private_pem"].encode("utf-8"),
            public_pem=jwt_data["public_pem"].encode("utf-8"),
            kid=jwt_data["kid"],
        ),
    )


def _validate_rd(rd_url: str, env_domain: str) -> bool:
    """Only accept redirects that stay within the env's parent domain."""
    parsed = urllib.parse.urlparse(rd_url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    parent = env_domain.lower()
    return host == parent or host.endswith("." + parent)


def _mint_state(rd_url: str, key: JwtKeyConfig) -> str:
    now = int(time.time())
    return jwt.encode(
        payload={
            "rd": rd_url,
            "iat": now,
            "exp": now + STATE_TTL_SECONDS,
            "nonce": secrets.token_urlsafe(16),
        },
        key=key.private_pem,
        algorithm=JWT_ALGORITHM,
        headers={"kid": key.kid},
    )


def _verify_state(state: str, key: JwtKeyConfig) -> str | None:
    """Verify the state JWT and return the embedded rd URL, or None on failure."""
    try:
        claims = jwt.decode(
            state, key=key.public_pem, algorithms=[JWT_ALGORITHM], leeway=5,
        )
    except jwt.PyJWTError as exc:
        logger.error("state reject: %s", exc)
        return None
    rd = claims.get("rd")
    if not isinstance(rd, str):
        logger.error("state reject reason=missing-rd")
        return None
    return rd


def _mint_session_jwt(
    oidc_sub: str,
    username: str,
    email: str,
    ttl_seconds: int,
    key: JwtKeyConfig,
) -> str:
    now = int(time.time())
    return jwt.encode(
        payload={
            "sub": oidc_sub,
            "username": username,
            "email": email,
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


async def _exchange_code_for_userinfo(
    code: str,
    redirect_uri: str,
    oidc: OidcConfig,
    http_client: httpx.AsyncClient,
) -> dict:
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
            f"okta token exchange failed status={token_response.status_code} "
            f"body={token_response.text[:200]!r}",
        )
    token_data = token_response.json()
    access_token = token_data["access_token"]

    userinfo_response = await http_client.get(
        url=f"{oidc.issuer_url}/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_response.status_code != 200:
        raise RuntimeError(
            f"okta userinfo failed status={userinfo_response.status_code} "
            f"body={userinfo_response.text[:200]!r}",
        )
    return userinfo_response.json()


def build_auth_router(cfg: config_mod.AuthServiceConfig) -> APIRouter:
    """Build the FastAPI router for the auth-service role. Caller mounts runtime on app.state."""
    router = APIRouter()

    @router.get("/start")
    async def start(request: Request) -> Response:
        rd = request.query_params.get("rd", "")
        if not rd or not _validate_rd(rd_url=rd, env_domain=cfg.env_domain):
            return PlainTextResponse(content="invalid rd parameter", status_code=400)

        runtime: AuthRuntimeConfig = request.app.state.auth_runtime
        state = _mint_state(rd_url=rd, key=runtime.jwt_key)
        authorize_url = (
            f"{runtime.oidc.issuer_url}/v1/authorize?"
            + urllib.parse.urlencode({
                "client_id": runtime.oidc.client_id,
                "response_type": "code",
                "scope": "openid email profile",
                "redirect_uri": f"{cfg.auth_base_url}/callback",
                "state": state,
            })
        )
        return RedirectResponse(url=authorize_url, status_code=302)

    @router.get("/callback")
    async def callback(request: Request) -> Response:
        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        if not code or not state:
            return PlainTextResponse(content="missing code or state", status_code=400)

        runtime: AuthRuntimeConfig = request.app.state.auth_runtime
        rd = _verify_state(state=state, key=runtime.jwt_key)
        if rd is None or not _validate_rd(rd_url=rd, env_domain=cfg.env_domain):
            return PlainTextResponse(content="invalid or expired state", status_code=400)

        try:
            userinfo = await _exchange_code_for_userinfo(
                code=code,
                redirect_uri=f"{cfg.auth_base_url}/callback",
                oidc=runtime.oidc,
                http_client=request.app.state.http_client,
            )
        except Exception as exc:
            logger.error("oidc exchange failed: %s", exc)
            return PlainTextResponse(content="oidc exchange failed", status_code=502)

        session_jwt = _mint_session_jwt(
            oidc_sub=userinfo["sub"],
            username=userinfo.get("email", ""),  # design ties username to Okta email
            email=userinfo.get("email", ""),
            ttl_seconds=cfg.session_ttl_seconds,
            key=runtime.jwt_key,
        )
        logger.info(
            "auth callback ok env=%s sub=%s email=%s -> %s",
            cfg.env_domain, userinfo["sub"], userinfo.get("email", ""), rd,
        )
        return Response(
            status_code=302,
            headers={
                "location": rd,
                "set-cookie": _session_cookie(
                    jwt_value=session_jwt, env_domain=cfg.env_domain, ttl_seconds=cfg.session_ttl_seconds,
                ),
            },
        )

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
