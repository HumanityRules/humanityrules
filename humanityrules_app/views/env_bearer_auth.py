"""Env bearer authentication for control-plane endpoints.

Shared by every HUMR endpoint that env-resident components call (PDP,
Google-token refresh, etc.). The caller sends `Authorization: Bearer
<raw_token>`; HUMR hashes it and looks up the matching
EnvironmentBearerToken row. The raw token lives only in the env's
shared-secrets entry; HUMR stores only the hash.
"""

import hashlib
import hmac

from django.http import HttpRequest

from humanityrules_app.models import Environment, EnvironmentBearerToken


def hash_token(raw: str) -> str:
    """Return the SHA-256 hex digest of *raw*."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_bearer_token(request: HttpRequest) -> str | None:
    """Return the raw bearer token from the Authorization header, or None."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header[len("Bearer "):].strip() or None


def resolve_env_from_token(raw_token: str) -> Environment | None:
    """Return the Environment whose EnvironmentBearerToken matches *raw_token*, or None.

    Constant-time match: look up by hash via the unique index, then
    compare digests with hmac.compare_digest so equality can't leak timing.
    """
    token_hash = hash_token(raw=raw_token)
    row = EnvironmentBearerToken.objects.select_related(
        "environment", "environment__aws_account__organization",
    ).filter(token_hash=token_hash).first()
    if row is None:
        return None
    if not hmac.compare_digest(row.token_hash, token_hash):
        return None
    return row.environment
