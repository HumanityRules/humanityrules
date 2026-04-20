"""
Sidecar auth Lambda — OAuth with Okta + session JWT minting.

Triggered by ALB via a host-based listener rule on auth.<env-domain>. See
docs/sidecar_proxy_design.md for the overall flow.

Routes:
- GET /start?rd=<url>               begin the OAuth dance
- GET /callback?code=...&state=...  exchange code + mint session JWT
- GET /.well-known/jwks.json        public key for sidecar verification
"""

import base64
import json
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass

import boto3
import jwt
import urllib3
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Signing config: we use RS256 to keep JWKS trivially interoperable. EdDSA
# works too but requires a newer pyjwt that may not be present in the runtime.
JWT_ALGORITHM = "RS256"
SESSION_TTL_SECONDS = 60 * 60  # 1 hour — design doc value
STATE_TTL_SECONDS = 10 * 60    # OAuth state param must be consumed within 10 minutes

# Module-scoped HTTP pool + boto client: reused across warm invocations.
_http = urllib3.PoolManager()
_secrets_client = None


# -----------------------------------------------------------------------------
# Config loading (cached across warm invocations)
# -----------------------------------------------------------------------------


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


_cached_oidc: OidcConfig | None = None
_cached_jwt_key: JwtKeyConfig | None = None


def _get_secrets_client():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


def _load_secret_json(secret_arn: str) -> dict:
    client = _get_secrets_client()
    response = client.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


def load_oidc_config() -> OidcConfig:
    global _cached_oidc
    if _cached_oidc is not None:
        return _cached_oidc
    arn = os.environ["DOH_OIDC_SECRET_ARN"]
    data = _load_secret_json(arn)
    _cached_oidc = OidcConfig(
        issuer_url=data["issuer_url"].rstrip("/"),
        client_id=data["client_id"],
        client_secret=data["client_secret"],
    )
    return _cached_oidc


def load_jwt_key() -> JwtKeyConfig:
    global _cached_jwt_key
    if _cached_jwt_key is not None:
        return _cached_jwt_key
    arn = os.environ["DOH_SIDECAR_JWT_SECRET_ARN"]
    data = _load_secret_json(arn)
    _cached_jwt_key = JwtKeyConfig(
        private_pem=data["private_pem"].encode("utf-8"),
        public_pem=data["public_pem"].encode("utf-8"),
        kid=data["kid"],
    )
    return _cached_jwt_key


def env_domain() -> str:
    return os.environ["DOH_ENV_DOMAIN"]


def auth_base_url() -> str:
    # The URL the sidecar redirects clients to. Registered as a single callback
    # URI on the Okta app: <auth_base_url>/callback.
    return os.environ["DOH_AUTH_BASE_URL"].rstrip("/")


# -----------------------------------------------------------------------------
# ALB <-> Lambda event helpers
# -----------------------------------------------------------------------------


def _alb_response(
    status_code: int,
    body: str,
    headers: dict[str, str] | None = None,
    is_base64: bool = False,
) -> dict:
    return {
        "statusCode": status_code,
        "statusDescription": f"{status_code} {_status_phrase(status_code)}",
        "isBase64Encoded": is_base64,
        "headers": {"content-type": "text/plain", **(headers or {})},
        "body": body,
    }


def _status_phrase(code: int) -> str:
    return {
        200: "OK", 302: "Found", 400: "Bad Request", 403: "Forbidden",
        404: "Not Found", 500: "Internal Server Error",
    }.get(code, "")


def _redirect(location: str) -> dict:
    return _alb_response(
        status_code=302,
        body="",
        headers={"location": location},
    )


def _query_params(event: dict) -> dict[str, str]:
    # ALB -> Lambda passes query-string values PERCENT-ENCODED in
    # queryStringParameters (e.g. "https%3A%2F%2Fapp..."). If we forward them
    # raw to urlparse, scheme comes out as "" and host as None, which made
    # _validate_rd reject every subdomain redirect. Always decode here so
    # callers get a clean URL regardless of which ALB target-group flavor is
    # in use.
    params = event.get("queryStringParameters") or {}
    multi = event.get("multiValueQueryStringParameters") or {}
    for k, v in multi.items():
        if k not in params and isinstance(v, list) and v:
            params[k] = v[0]
    return {k: urllib.parse.unquote(v) for k, v in params.items()}


def _path(event: dict) -> str:
    return event.get("path", "/")


# -----------------------------------------------------------------------------
# State param — carries the post-login redirect destination, signed so we can
# trust it on the callback.
# -----------------------------------------------------------------------------


def _mint_state(rd_url: str) -> str:
    now = int(time.time())
    key = load_jwt_key()
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


def _verify_state(state: str) -> str | None:
    """Verify the state JWT and return the embedded rd URL, or None on failure."""
    key = load_jwt_key()
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


def _validate_rd(rd_url: str) -> bool:
    """Only accept redirects that stay within the env's parent domain."""
    parsed = urllib.parse.urlparse(rd_url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    parent = env_domain().lower()
    return host == parent or host.endswith("." + parent)


# -----------------------------------------------------------------------------
# OAuth exchange with Okta
# -----------------------------------------------------------------------------


def _exchange_code_for_userinfo(code: str, redirect_uri: str) -> dict:
    cfg = load_oidc_config()

    token_response = _http.request(
        "POST",
        f"{cfg.issuer_url}/v1/token",
        fields={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
        },
        encode_multipart=False,
    )
    if token_response.status != 200:
        raise RuntimeError(
            f"okta token exchange failed status={token_response.status} body={token_response.data[:200]!r}",
        )
    token_data = json.loads(token_response.data)
    access_token = token_data["access_token"]

    userinfo_response = _http.request(
        "GET",
        f"{cfg.issuer_url}/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_response.status != 200:
        raise RuntimeError(
            f"okta userinfo failed status={userinfo_response.status} body={userinfo_response.data[:200]!r}",
        )
    return json.loads(userinfo_response.data)


# -----------------------------------------------------------------------------
# Session JWT (the doh_session cookie)
# -----------------------------------------------------------------------------


def _mint_session_jwt(oidc_sub: str, username: str, email: str) -> str:
    now = int(time.time())
    key = load_jwt_key()
    return jwt.encode(
        payload={
            "sub": oidc_sub,
            "username": username,
            "email": email,
            "iat": now,
            "exp": now + SESSION_TTL_SECONDS,
        },
        key=key.private_pem,
        algorithm=JWT_ALGORITHM,
        headers={"kid": key.kid},
    )


def _session_cookie(jwt_value: str) -> str:
    return (
        f"doh_session={jwt_value}; Domain=.{env_domain()}; Path=/; "
        f"Max-Age={SESSION_TTL_SECONDS}; Secure; HttpOnly; SameSite=Lax"
    )


# -----------------------------------------------------------------------------
# JWKS endpoint
# -----------------------------------------------------------------------------


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


def _b64url_uint(n: int) -> str:
    # Smallest big-endian byte representation, base64url-encoded, no padding.
    length = (n.bit_length() + 7) // 8
    raw = n.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _handle_jwks() -> dict:
    key = load_jwt_key()
    body = json.dumps({"keys": [_jwk_from_pem(key.public_pem, key.kid)]})
    return _alb_response(
        status_code=200,
        body=body,
        headers={"content-type": "application/json", "cache-control": "public, max-age=300"},
    )


# -----------------------------------------------------------------------------
# Route handlers
# -----------------------------------------------------------------------------


def _handle_start(event: dict) -> dict:
    params = _query_params(event)
    rd = params.get("rd", "")
    if not rd or not _validate_rd(rd):
        return _alb_response(
            status_code=400, body="invalid rd parameter",
        )

    cfg = load_oidc_config()
    state = _mint_state(rd_url=rd)
    authorize_url = (
        f"{cfg.issuer_url}/v1/authorize?"
        + urllib.parse.urlencode({
            "client_id": cfg.client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": f"{auth_base_url()}/callback",
            "state": state,
        })
    )
    return _redirect(authorize_url)


def _handle_callback(event: dict) -> dict:
    params = _query_params(event)
    code = params.get("code", "")
    state = params.get("state", "")
    if not code or not state:
        return _alb_response(
            status_code=400, body="missing code or state",
        )

    rd = _verify_state(state)
    if rd is None or not _validate_rd(rd):
        return _alb_response(
            status_code=400, body="invalid or expired state",
        )

    try:
        userinfo = _exchange_code_for_userinfo(
            code=code, redirect_uri=f"{auth_base_url()}/callback",
        )
    except Exception as exc:
        logger.error("oidc exchange failed: %s", exc)
        return _alb_response(
            status_code=502, body="oidc exchange failed",
        )

    session_jwt = _mint_session_jwt(
        oidc_sub=userinfo["sub"],
        username=userinfo.get("email", ""),  # design ties username to Okta email
        email=userinfo.get("email", ""),
    )
    logger.info(
        "auth callback ok env=%s sub=%s email=%s -> %s",
        env_domain(), userinfo["sub"], userinfo.get("email", ""), rd,
    )
    return _alb_response(
        status_code=302,
        body="",
        headers={
            "location": rd,
            "set-cookie": _session_cookie(session_jwt),
        },
    )


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def handler(event: dict, context) -> dict:
    path = _path(event)
    method = event.get("httpMethod", "GET")
    logger.info("auth lambda %s %s", method, path)

    if path == "/start":
        return _handle_start(event)
    if path == "/callback":
        return _handle_callback(event)
    if path == "/.well-known/jwks.json":
        return _handle_jwks()

    return _alb_response(status_code=404, body="not found")
