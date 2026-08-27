"""Bearer authentication for the control-plane endpoints that env-resident components call.

Everything a deployed app calls on HUMR — PDP decisions, activity reports, the
integrations broker — authenticates the same way: the caller sends
`Authorization: Bearer <raw_token>` and HUMR looks the token's SHA-256 hash up
in the DB. The raw value never leaves the customer's AWS account; HUMR stores
only the hash, so it can verify a presentation but never reproduce one.

The token being resolved is per-App (`AppBearerToken`, raw value in
`humr/{env}/{app}/secrets` under `HUMR_APP_BEARER`), which is what lets a view
*derive* the calling App — and through it the Environment, Organization and
owner — instead of trusting an app_slug / owner_username the caller put in the
request. `resolve_env_from_token` is the retired per-environment path, kept only
until its last callers move over.
"""

import hashlib
import hmac

from django.http import HttpRequest

from humanityrules_app.models import App, AppBearerToken, Environment, EnvironmentBearerToken


def hash_token(raw: str) -> str:
    """Return the SHA-256 hex digest of *raw*."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_bearer_token(request: HttpRequest) -> str | None:
    """Return the raw bearer token from the Authorization header, or None."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header[len("Bearer "):].strip() or None


def resolve_app_from_token(raw_token: str) -> App | None:
    """Return the App whose AppBearerToken matches *raw_token*, or None.

    Constant-time match: look up by hash via the unique index, then compare
    digests with hmac.compare_digest so equality can't leak timing.
    """
    token_hash = hash_token(raw=raw_token)
    row = AppBearerToken.objects.select_related(
        "app__environment__aws_account__organization",
    ).filter(token_hash=token_hash).first()
    if row is None:
        return None
    if not hmac.compare_digest(row.token_hash, token_hash):
        return None
    return row.app


def resolve_env_from_token(raw_token: str) -> Environment | None:
    """Return the Environment whose EnvironmentBearerToken matches *raw_token*, or None.

    Legacy per-environment path — the token names only the environment, so every
    caller of this has to take the App's identity on trust from the request.
    Deleted once the broker family moves to resolve_app_from_token.
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
