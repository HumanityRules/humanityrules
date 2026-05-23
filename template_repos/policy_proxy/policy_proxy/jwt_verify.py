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


@dataclass(frozen=True)
class SessionIdentity:
    """The verified claims we care about from a session JWT.

    ``sub`` is the IdP's stable identifier — for ``provider="workos"`` it's
    the WorkOS user id, for ``provider="oidc"`` it's the OIDC subject claim.
    The PDP needs ``provider`` to know which User column to look up against.
    """
    sub: str
    provider: str
    username: str
    email: str


def verify_session_jwt(*, jwt_value: str, jwks_client: jwt.PyJWKClient, env_domain: str) -> SessionIdentity | None:
    """Verify a session JWT. ``aud`` must equal ``env_domain`` to block cross-env replay.

    The env's DNS zone is globally unique (one per env), unlike ``env_slug`` which is only
    unique per AWS account. With a single central JWKS, only a globally unique audience
    can prevent replay across envs that happen to share a slug in different accounts.
    """
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
            audience=env_domain,
            options={"require": ["exp", "iat", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        logger.error("jwt reject reason=invalid err=%s", exc)
        return None

    sub = claims.get("sub")
    username = claims.get("username")
    email = claims.get("email", "")
    provider = claims.get("provider")
    if not (
        isinstance(sub, str) and isinstance(username, str)
        and isinstance(provider, str) and provider in ("workos", "oidc")
    ):
        logger.error("jwt reject reason=missing-claim")
        return None

    return SessionIdentity(sub=sub, provider=provider, username=username, email=email)


def session_jwt_exp(jwt_value: str) -> int | None:
    """Return the ``exp`` claim from an *unverified* JWT, for cookie Max-Age sizing."""
    try:
        claims = jwt.decode(jwt_value, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    exp = claims.get("exp")
    if not isinstance(exp, int):
        return None
    return exp
