"""JWT verification for the doh_session cookie, backed by PyJWKClient."""

import logging
from dataclasses import dataclass

import jwt

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "doh_session"
# Expected signing algorithms. Keeping this tight prevents "alg=none" downgrade.
ACCEPTED_ALGORITHMS = ["RS256", "EdDSA"]
# JWT clock skew tolerance, seconds. ECS task clocks are usually tight; this is
# a small safety margin against normal NTP drift.
LEEWAY_SECONDS = 30
ACCEPTED_PROVIDERS = {"oidc", "workos"}


@dataclass(frozen=True)
class SessionIdentity:
    """The verified claims we care about from a session JWT.

    ``sub`` is opaque from the proxy's point of view — its meaning depends on
    ``provider``. The PDP uses both fields together to look up a User row.
    """
    sub: str
    username: str
    email: str
    provider: str


def verify_session_cookie(jwt_value: str, jwks_client: jwt.PyJWKClient) -> SessionIdentity | None:
    """Verify a JWT cookie. Returns claims on success, None on failure (log + redirect)."""
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(jwt_value).key
    except jwt.PyJWTError as exc:
        logger.error("jwt reject reason=jwks-lookup err=%s", exc)
        return None

    try:
        claims = jwt.decode(
            jwt_value,
            key=signing_key,
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
    provider = claims.get("provider")
    if not (isinstance(sub, str) and isinstance(username, str) and isinstance(provider, str)):
        logger.error("jwt reject reason=missing-claim")
        return None
    if provider not in ACCEPTED_PROVIDERS:
        logger.error("jwt reject reason=unknown-provider provider=%r", provider)
        return None

    return SessionIdentity(sub=sub, username=username, email=email, provider=provider)
