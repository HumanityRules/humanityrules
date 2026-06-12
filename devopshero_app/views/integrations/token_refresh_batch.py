"""Batched integration token refresh for env-resident Hermes brokers.

POST /api/integrations/tokens (env bearer auth) returns a per-slug outcome
map for the requested providers. The Hermes integration broker calls this at
bootstrap (Refresh-all) and after connect/disconnect (single-slug list).

    body: {owner_username, app_slug, providers: ["google", "github", ...]}
    resp: 200 {results: {<slug>: {outcome, secrets?, expires_in?, config, metadata}, ...}}

Each slug routes to a provider outcome helper (OAuth token exchange or vault
DB read). Helpers run in parallel via ThreadPoolExecutor so wall-clock time
is bounded by the slowest helper (~5s upstream ceiling per OAuth provider),
not the sum across slugs.

`outcome` is `has_token`, `absent`, or `transient`. `absent` means no
connected integration row — a normal 200 entry, not an HTTP error. Unknown
slugs and users with no org membership on the env also return `absent`.
Handler exceptions surface as `transient` so the broker can keep serving a
still-valid cached token when refresh fails transiently.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import Environment, User
from devopshero_app.views.integrations import broker_request_context, provider_registry

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def integrations_tokens_batch(request: HttpRequest) -> JsonResponse:
    """Refresh many providers in one round-trip; `absent` is a normal result, not 404."""
    environment, auth_error = broker_request_context.resolve_env_bearer_context(request=request)
    if auth_error is not None:
        logger.error("batched token refresh: env bearer auth failed")
        return auth_error
    payload, parse_error = broker_request_context.parse_json_body(request=request)
    if parse_error is not None:
        logger.error("batched token refresh: invalid JSON body env=%s", environment.slug)
        return parse_error

    owner_username = payload.get("owner_username")
    if not isinstance(owner_username, str) or not owner_username:
        logger.error("batched token refresh: missing owner_username env=%s", environment.slug)
        return JsonResponse({"error": "owner_username is required"}, status=400)
    app_slug = payload.get("app_slug")
    if not isinstance(app_slug, str) or not app_slug:
        logger.error("batched token refresh: missing app_slug env=%s owner=%s", environment.slug, owner_username)
        return JsonResponse({"error": "app_slug is required"}, status=400)
    requested_providers = payload.get("providers")
    if not isinstance(requested_providers, list) or not requested_providers:
        logger.error(
            "batched token refresh: empty providers env=%s owner=%s app=%s",
            environment.slug, owner_username, app_slug,
        )
        return JsonResponse({"error": "providers must be a non-empty list"}, status=400)
    for provider in requested_providers:
        if not isinstance(provider, str) or not provider:
            logger.error(
                "batched token refresh: invalid provider entry env=%s owner=%s app=%s providers=%s",
                environment.slug, owner_username, app_slug, requested_providers,
            )
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

    logger.info(
        "batched token refresh: env=%s owner=%s app=%s providers=%s",
        environment.slug, owner_username, app_slug, requested_providers,
    )

    # Each helper does DB reads + (for OAuth providers) an outbound HTTPS
    # refresh to the upstream provider. Run them in parallel so worst-case
    # wall-clock is one slow provider, not the sum across providers. Each
    # helper's upstream timeout is 5s, so the whole batch is naturally
    # bounded by ~5s + DB / marshalling overhead — well inside the
    # broker's 7s urlopen ceiling, no separate batch deadline needed.
    specs_to_run = {
        slug: spec
        for slug in requested_providers
        if (spec := provider_registry.get(provider=slug)) is not None
    }
    unknown_slugs = [slug for slug in requested_providers if slug not in specs_to_run]
    if unknown_slugs:
        logger.info(
            "batched token refresh: unknown provider slugs env=%s owner=%s app=%s slugs=%s",
            environment.slug, owner_username, app_slug, unknown_slugs,
        )
    results: dict[str, dict] = {slug: {"outcome": "absent"} for slug in unknown_slugs}
    if specs_to_run:
        with ThreadPoolExecutor(max_workers=len(specs_to_run)) as executor:
            future_to_slug = {
                executor.submit(_run_handler, spec.module.refresh_outcome, environment, user, app_slug): slug
                for slug, spec in specs_to_run.items()
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
    logger.info(
        "batched token refresh: done env=%s owner=%s app=%s outcomes=%s",
        environment.slug,
        owner_username,
        app_slug,
        {slug: results[slug]["outcome"] for slug in requested_providers},
    )
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
