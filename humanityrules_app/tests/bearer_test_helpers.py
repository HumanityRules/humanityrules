"""Fixtures for the per-app app bearer token.

Every endpoint a deployed app calls authenticates with an AppBearerToken, so
nearly every API test suite needs to mint one and put it on a request. These
helpers keep that in one place instead of each suite re-deriving the hash.

The raw value is arbitrary — the control plane only ever compares hashes — so
tests pass whatever string reads well at the call site.
"""

from humanityrules_app.models import App, AppBearerToken
from humanityrules_app.views import app_bearer_auth


def make_app_bearer(app: App, raw: str) -> str:
    """Create (or replace) the App's bearer token row for *raw* and return *raw*."""
    AppBearerToken.objects.update_or_create(
        app=app,
        defaults={"token_hash": app_bearer_auth.hash_token(raw=raw)},
    )
    return raw


def auth_header(raw: str) -> dict[str, str]:
    """Django test-client kwargs carrying *raw* as the bearer token."""
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
