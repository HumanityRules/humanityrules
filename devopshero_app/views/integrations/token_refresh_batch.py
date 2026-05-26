"""The single env-resident-component → DOH refresh endpoint.

The broker used to fan out one POST per provider to per-provider
`/api/integrations/<slug>/token` endpoints; each disconnected provider
returned HTTP 404 + an `INFO no integration row` Django log line.
Refresh-all on a fresh sandbox routinely emitted three logs that all
meant "the user hasn't connected anything yet" — log spam masquerading
as signal.

This endpoint folds all of that into one round-trip with `absent` as a
normal entry in the response map:

    POST /api/integrations/tokens
    body: {owner_username, app_slug, providers: ["google", "github", ...]}
    resp: 200 {results: {<slug>: {outcome, access_token?, expires_in?, config, metadata}, ...}}

`outcome` is `"has_token" | "absent" | "transient"`. The broker uses the
same endpoint for single-slug refresh (after a connect/disconnect),
just with a one-element providers list.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import Environment, User
from devopshero_app.views import env_bearer_auth
from devopshero_app.views.integrations import (
    github_token_refresh,
    google_token_refresh,
    user_credential_vault,
)

logger = logging.getLogger(__name__)


_OUTCOME_HANDLERS = {
    "google": google_token_refresh.refresh_google_outcome,
    "github": github_token_refresh.refresh_github_outcome,
    "telegram": user_credential_vault.refresh_telegram_outcome,
}


@csrf_exempt
@require_POST
def integrations_tokens_batch(request: HttpRequest) -> JsonResponse:
    """Refresh many providers in one round-trip; `absent` is a normal result, not 404."""
    raw_token = env_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return JsonResponse({"error": "missing bearer token"}, status=401)
    environment = env_bearer_auth.resolve_env_from_token(raw_token=raw_token)
    if environment is None:
        return JsonResponse({"error": "invalid bearer token"}, status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    owner_username = payload.get("owner_username")
    if not isinstance(owner_username, str) or not owner_username:
        return JsonResponse({"error": "owner_username is required"}, status=400)
    app_slug = payload.get("app_slug")
    if not isinstance(app_slug, str) or not app_slug:
        return JsonResponse({"error": "app_slug is required"}, status=400)
    requested_providers = payload.get("providers")
    if not isinstance(requested_providers, list) or not requested_providers:
        return JsonResponse({"error": "providers must be a non-empty list"}, status=400)
    for provider in requested_providers:
        if not isinstance(provider, str) or not provider:
            return JsonResponse({"error": "providers must be non-empty strings"}, status=400)

    user = User.objects.filter(
        username=owner_username,
        organization_memberships__organization=environment.aws_account.organization,
    ).first()
    if user is None:
        # No org/user binding for this env+username; surface as absent for
        # every provider so the broker treats them as disconnected. Avoid
        # logging — a stale username after an org membership change should
        # not look like an error from this endpoint's perspective.
        return JsonResponse({"results": {slug: {"outcome": "absent"} for slug in requested_providers}})

    # Each helper does DB reads + (for OAuth providers) an outbound HTTPS
    # refresh to the upstream provider. Run them in parallel so worst-case
    # wall-clock is one slow provider, not the sum across providers. Each
    # helper's upstream timeout is 5s, so the whole batch is naturally
    # bounded by ~5s + DB / marshalling overhead — well inside the
    # broker's 7s urlopen ceiling, no separate batch deadline needed.
    handlers_to_run = {
        slug: _OUTCOME_HANDLERS[slug]
        for slug in requested_providers
        if slug in _OUTCOME_HANDLERS
    }
    results: dict[str, dict] = {
        slug: {"outcome": "absent"}
        for slug in requested_providers
        if slug not in _OUTCOME_HANDLERS
    }
    if handlers_to_run:
        with ThreadPoolExecutor(max_workers=len(handlers_to_run)) as executor:
            future_to_slug = {
                executor.submit(_run_handler, handler, environment, user, app_slug): slug
                for slug, handler in handlers_to_run.items()
            }
            for future in future_to_slug:
                slug = future_to_slug[future]
                try:
                    results[slug] = future.result()
                except Exception:
                    logger.exception(
                        "batched token refresh failed for provider=%s env=%s owner=%s app=%s",
                        slug, environment.slug, owner_username, app_slug,
                    )
                    results[slug] = {"outcome": "transient"}
    return JsonResponse({"results": results})


def _run_handler(handler, environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Run a per-provider outcome helper on a worker thread and clean up its DB connection."""
    try:
        return handler(environment=environment, owner_user=owner_user, app_slug=app_slug)
    finally:
        # `close_old_connections` is the canonical Django primitive for ad-hoc
        # threads that touch the ORM — without it the worker's per-thread DB
        # connection lingers until the ThreadPoolExecutor itself is GC'd.
        close_old_connections()
