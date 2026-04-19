"""JWKS fetching and per-kid public key caching."""

import logging
import time
from dataclasses import dataclass

import httpx
import jwt

logger = logging.getLogger(__name__)


@dataclass
class JwksCache:
    """Cached JWKS, refreshed periodically and on unknown-kid verification failure."""
    jwks_url: str
    ttl_seconds: int
    _keys_by_kid: dict[str, object]
    _fetched_at: float

    @classmethod
    def empty(cls, jwks_url: str, ttl_seconds: int) -> "JwksCache":
        return cls(jwks_url=jwks_url, ttl_seconds=ttl_seconds, _keys_by_kid={}, _fetched_at=0.0)

    def is_expired(self, now: float) -> bool:
        return (now - self._fetched_at) > self.ttl_seconds

    def get(self, kid: str) -> object | None:
        return self._keys_by_kid.get(kid)

    def replace(self, keys_by_kid: dict[str, object], now: float) -> None:
        self._keys_by_kid = keys_by_kid
        self._fetched_at = now


def _parse_jwks(jwks_json: dict) -> dict[str, object]:
    """Convert a JWKS JSON blob to a {kid: cryptography_public_key} map."""
    result: dict[str, object] = {}
    for k in jwks_json.get("keys", []):
        kid = k.get("kid")
        if not kid:
            continue
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(k) if k.get("kty") == "RSA" \
            else jwt.algorithms.ECAlgorithm.from_jwk(k) if k.get("kty") == "EC" \
            else jwt.algorithms.OKPAlgorithm.from_jwk(k) if k.get("kty") == "OKP" \
            else None
        if public_key is None:
            logger.error("jwks skipping unsupported key kty=%s kid=%s", k.get("kty"), kid)
            continue
        result[kid] = public_key
    return result


async def refresh_jwks(cache: JwksCache, http_client: httpx.AsyncClient, now: float) -> None:
    """Fetch the JWKS and replace the cache contents."""
    response = await http_client.get(cache.jwks_url, timeout=5.0)
    response.raise_for_status()
    keys = _parse_jwks(response.json())
    cache.replace(keys_by_kid=keys, now=now)
    logger.info("jwks refreshed count=%d", len(keys))


async def get_public_key(
    cache: JwksCache, http_client: httpx.AsyncClient, kid: str, now_fn=time.time,
) -> object | None:
    """Return the public key for *kid*, refreshing the cache if expired or missing."""
    key = cache.get(kid)
    if key is not None and not cache.is_expired(now_fn()):
        return key
    try:
        await refresh_jwks(cache=cache, http_client=http_client, now=now_fn())
    except Exception as exc:
        logger.error("jwks refresh failed: %s", exc)
        return cache.get(kid)  # may still be None
    return cache.get(kid)
