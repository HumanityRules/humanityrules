"""JWT verification for the doh_session cookie."""

import logging
from dataclasses import dataclass

import httpx
import jwt

from . import jwks as jwks_mod

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "doh_session"
# Expected signing algorithms. Keeping this tight prevents "alg=none" downgrade.
ACCEPTED_ALGORITHMS = ["RS256", "EdDSA"]
# JWT clock skew tolerance, seconds. ECS task clocks are usually tight; this is
# a small safety margin against normal NTP drift.
LEEWAY_SECONDS = 30


@dataclass(frozen=True)
class SessionIdentity:
    """The verified claims we care about from a session JWT."""
    oidc_sub: str
    username: str
    email: str


async def verify_session_cookie(
    jwt_value: str,
    jwks_cache: jwks_mod.JwksCache,
    http_client: httpx.AsyncClient,
) -> SessionIdentity | None:
    """Verify a JWT cookie. Returns claims on success, None on failure (log + redirect)."""
    try:
        unverified_header = jwt.get_unverified_header(jwt_value)
    except jwt.PyJWTError as exc:
        logger.error("jwt reject reason=malformed-header err=%s", exc)
        return None

    kid = unverified_header.get("kid")
    if not kid:
        logger.error("jwt reject reason=missing-kid")
        return None

    public_key = await jwks_mod.get_public_key(
        cache=jwks_cache, http_client=http_client, kid=kid,
    )
    if public_key is None:
        logger.error("jwt reject reason=unknown-kid kid=%s", kid)
        return None

    try:
        claims = jwt.decode(
            jwt_value,
            key=public_key,
            algorithms=ACCEPTED_ALGORITHMS,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        logger.error("jwt reject reason=invalid err=%s", exc)
        return None

    sub = claims.get("sub")
    username = claims.get("username")
    email = claims.get("email", "")
    if not (isinstance(sub, str) and isinstance(username, str)):
        logger.error("jwt reject reason=missing-claim")
        return None

    return SessionIdentity(oidc_sub=sub, username=username, email=email)
