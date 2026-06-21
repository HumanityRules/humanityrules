"""Env-resident-component → DOH endpoints for Merge.dev Agent Handler.

The Merge tenant-wide API key lives on DOH only — never inside the customer's
Hermes container. Env-resident callers (the integrations broker / MCP
aggregator) reach Merge through these endpoints, authenticating with their
DOH_ENV_BEARER. DOH validates the bearer, derives `origin_user_id` from the
authenticated env's owner + app slug, attaches the Merge API key, and
forwards.

`origin_user_id = f"doh_{owner.pk}_{app_slug}"`. Per-app, not per-user. Same
user destroying/recreating the same slug keeps integrations; a different user
taking over the slug starts fresh.
"""

import json
import logging
import re

import httpx
from django.conf import settings
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from humanityrules_app.models import App, ResourceTag, User
from humanityrules_app.views import env_bearer_auth

logger = logging.getLogger(__name__)


MERGE_API_BASE = "https://ah-api.merge.dev"
MERGE_REQUEST_TIMEOUT_SECONDS = 30
# MCP responses are streamed and may be long-lived. Keep upstream connect
# timeout tight, but read timeout permissive.
MERGE_MCP_CONNECT_TIMEOUT_SECONDS = 10
MERGE_MCP_READ_TIMEOUT_SECONDS = 600

# Merge POST /registered-users is NOT idempotent; on duplicate it returns 400
# with `{"non_field_errors": ["User of origin_id: ... already exists. ... PATCH
# /registered-users/<UUID> endpoint."]}`. We parse the UUID out of that string
# rather than maintaining a DB cache — the operation is rare and bounded.
_DUPLICATE_USER_UUID_RE = re.compile(
    r"already exists.*registered-users/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _origin_user_id(*, user: User, app_slug: str) -> str:
    return f"doh_{user.pk}_{app_slug}"


def _resolve_caller(request: HttpRequest) -> tuple[App, User] | JsonResponse:
    """Validate the bearer and resolve the (App, User) pair the call is for.

    Identity is read first from `X-Doh-App-Slug` / `X-Doh-Owner-Username`
    headers (used by the MCP relay, where the body is the JSON-RPC payload),
    and falls back to query string / JSON body for the other endpoints.
    """
    raw_token = env_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return JsonResponse({"error": "missing bearer token"}, status=401)
    environment = env_bearer_auth.resolve_env_from_token(raw_token=raw_token)
    if environment is None:
        return JsonResponse({"error": "invalid bearer token"}, status=401)

    app_slug = request.headers.get("X-Doh-App-Slug", "")
    owner_username = request.headers.get("X-Doh-Owner-Username", "")
    if not app_slug or not owner_username:
        if request.method == "GET":
            app_slug = app_slug or request.GET.get("app_slug", "")
            owner_username = owner_username or request.GET.get("owner_username", "")
        else:
            try:
                payload = json.loads(request.body) if request.body else {}
            except json.JSONDecodeError:
                payload = {}
            app_slug = app_slug or payload.get("app_slug", "")
            owner_username = owner_username or payload.get("owner_username", "")

    if not isinstance(app_slug, str) or not app_slug:
        return JsonResponse({"error": "app_slug is required"}, status=400)
    if not isinstance(owner_username, str) or not owner_username:
        return JsonResponse({"error": "owner_username is required"}, status=400)

    organization = environment.aws_account.organization
    user = User.objects.filter(
        username=owner_username,
        organization_memberships__organization=organization,
    ).first()
    if user is None:
        return JsonResponse({"error": "owner user not found"}, status=404)

    app = App.objects.filter(organization=organization, slug=app_slug).first()
    if app is None:
        return JsonResponse({"error": "app not found in env's organization"}, status=404)
    owner_tag_exists = ResourceTag.objects.filter(
        organization=organization,
        resource_type=ResourceTag.ResourceType.APP,
        app=app,
        key="owner",
        value=user.username,
    ).exists()
    if not owner_tag_exists:
        return JsonResponse({"error": "app is not owned by requested user"}, status=403)

    return app, user


def _api_key_or_500() -> str | JsonResponse:
    api_key = settings.MERGE_AGENT_HANDLER_API_KEY
    if not api_key:
        logger.error("MERGE_AGENT_HANDLER_API_KEY is not configured on DOH")
        return JsonResponse({"error": "merge integration not configured"}, status=500)
    return api_key


def _tool_pack_or_500() -> str | JsonResponse:
    pack_id = settings.MERGE_TOOL_PACK_ID
    if not pack_id:
        logger.error("MERGE_TOOL_PACK_ID is not configured on DOH")
        return JsonResponse({"error": "merge tool pack not configured"}, status=500)
    return pack_id


def _ensure_registered_user_remote(*, user: User, app_slug: str, api_key: str) -> tuple[str | None, str | None]:
    """POST /api/v1/registered-users on Merge.

    Merge does NOT make this endpoint idempotent — duplicate POSTs return 400
    with the existing UUID embedded in `non_field_errors`. We parse it out.

    Returns `(registered_user_id, error_message)` — exactly one is None.
    """
    body = {
        "origin_user_id": _origin_user_id(user=user, app_slug=app_slug),
        "origin_user_name": app_slug,
    }
    try:
        response = httpx.post(
            f"{MERGE_API_BASE}/api/v1/registered-users/",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=MERGE_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return None, f"network: {exc}"

    if response.status_code in (200, 201):
        try:
            rid = response.json().get("id")
        except ValueError:
            return None, "non-json response on create"
        if not isinstance(rid, str) or not rid:
            return None, "missing id in create response"
        return rid, None

    # Duplicate path — extract the existing UUID from the error message.
    if response.status_code == 400:
        try:
            errs = response.json().get("non_field_errors") or []
        except ValueError:
            errs = []
        for msg in errs:
            match = _DUPLICATE_USER_UUID_RE.search(msg)
            if match:
                return match.group(1), None

    return None, f"http {response.status_code}: {response.text[:300]}"


def _fetch_registered_user(*, registered_user_id: str, api_key: str) -> tuple[dict | None, str | None]:
    """GET /api/v1/registered-users/{id}/ — returns the full record including authenticated_connectors."""
    try:
        response = httpx.get(
            f"{MERGE_API_BASE}/api/v1/registered-users/{registered_user_id}/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=MERGE_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return None, f"network: {exc}"
    if response.status_code != 200:
        return None, f"http {response.status_code}: {response.text[:300]}"
    try:
        return response.json(), None
    except ValueError:
        return None, "non-json response"


@csrf_exempt
@require_POST
def integrations_merge_ensure_registered_user(request: HttpRequest) -> JsonResponse:
    """Idempotently ensure a Merge Registered User exists for this app."""
    resolved = _resolve_caller(request=request)
    if isinstance(resolved, JsonResponse):
        return resolved
    app, user = resolved

    api_key = _api_key_or_500()
    if isinstance(api_key, JsonResponse):
        return api_key

    rid, error = _ensure_registered_user_remote(user=user, app_slug=app.slug, api_key=api_key)
    if error is not None:
        logger.error("merge ensure-registered-user failed: %s", error)
        return JsonResponse({"error": "merge ensure failed"}, status=502)
    return JsonResponse({"registered_user_id": rid})


@csrf_exempt
@require_POST
def integrations_merge_link_token(request: HttpRequest) -> JsonResponse:
    """Mint a Magic Link for one connector. No callback_url (Path A — generic completion page)."""
    resolved = _resolve_caller(request=request)
    if isinstance(resolved, JsonResponse):
        return resolved
    app, user = resolved

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)
    connector_slug = payload.get("connector_slug")
    if connector_slug is not None and (not isinstance(connector_slug, str) or not connector_slug):
        return JsonResponse({"error": "connector_slug must be a non-empty string"}, status=400)

    api_key = _api_key_or_500()
    if isinstance(api_key, JsonResponse):
        return api_key

    rid, error = _ensure_registered_user_remote(user=user, app_slug=app.slug, api_key=api_key)
    if error is not None:
        logger.error("merge link-token: ensure failed: %s", error)
        return JsonResponse({"error": "merge ensure failed"}, status=502)

    if not connector_slug:
        return JsonResponse({"error": "connector_slug is required"}, status=400)
    body = {"connector": connector_slug}
    try:
        response = httpx.post(
            f"{MERGE_API_BASE}/api/v1/registered-users/{rid}/link-token/",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=MERGE_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("merge link-token network failure: %s", exc)
        return JsonResponse({"error": "merge upstream error"}, status=502)
    if response.status_code not in (200, 201):
        logger.error("merge link-token http %d: %s", response.status_code, response.text[:300])
        return JsonResponse({"error": "merge link-token failed"}, status=502)
    try:
        data = response.json()
    except ValueError:
        return JsonResponse({"error": "merge non-json response"}, status=502)
    return JsonResponse({
        "magic_link_url": data.get("magic_link_url"),
        "link_token": data.get("link_token"),
    })


def _fetch_tool_pack_connectors(*, api_key: str, tool_pack_id: str) -> tuple[list[dict] | None, str | None]:
    try:
        response = httpx.get(
            f"{MERGE_API_BASE}/api/v1/tool-packs/{tool_pack_id}/connectors/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=MERGE_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return None, f"network: {exc}"
    if response.status_code != 200:
        return None, f"http {response.status_code}: {response.text[:300]}"
    try:
        data = response.json()
    except ValueError:
        return None, "non-json response"
    # The endpoint returns a flat list per the OpenAPI spec for ConnectorPublic.
    if isinstance(data, list):
        return data, None
    return None, "unexpected catalog shape"


def _authenticated_connectors(*, api_key: str, registered_user_id: str) -> tuple[set[str] | None, str | None]:
    """Pull the `authenticated_connectors` array off the Registered User record."""
    record, error = _fetch_registered_user(registered_user_id=registered_user_id, api_key=api_key)
    if error is not None:
        return None, error
    raw = record.get("authenticated_connectors") or []
    return {slug for slug in raw if isinstance(slug, str)}, None


@csrf_exempt
@require_GET
def integrations_merge_connectors(request: HttpRequest) -> JsonResponse:
    """Catalog of Tool Pack connectors merged with this user's connection state."""
    resolved = _resolve_caller(request=request)
    if isinstance(resolved, JsonResponse):
        return resolved
    app, user = resolved

    api_key = _api_key_or_500()
    if isinstance(api_key, JsonResponse):
        return api_key
    pack_id = _tool_pack_or_500()
    if isinstance(pack_id, JsonResponse):
        return pack_id

    rid, error = _ensure_registered_user_remote(user=user, app_slug=app.slug, api_key=api_key)
    if error is not None:
        logger.error("merge connectors: ensure failed: %s", error)
        return JsonResponse({"error": "merge ensure failed"}, status=502)

    connectors, cat_error = _fetch_tool_pack_connectors(api_key=api_key, tool_pack_id=pack_id)
    if cat_error is not None:
        logger.error("merge tool-pack catalog failed: %s", cat_error)
        return JsonResponse({"error": "merge catalog failed"}, status=502)
    connected, conn_error = _authenticated_connectors(api_key=api_key, registered_user_id=rid)
    if conn_error is not None:
        logger.error("merge user connections failed: %s", conn_error)
        return JsonResponse({"error": "merge connections failed"}, status=502)

    items = []
    for connector in connectors:
        slug = connector.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        items.append({
            "slug": slug,
            "name": connector.get("name", slug),
            "logo_url": connector.get("logo_url"),
            "status": "connected" if slug in connected else "not_connected",
        })
    return JsonResponse({"connectors": items})


@csrf_exempt
@require_GET
def integrations_merge_connector_status(request: HttpRequest) -> JsonResponse:
    """Per-connector status — used by 3-second polling during the connect modal."""
    resolved = _resolve_caller(request=request)
    if isinstance(resolved, JsonResponse):
        return resolved
    app, user = resolved

    connector_slug = request.GET.get("connector_slug", "")
    if not connector_slug:
        return JsonResponse({"error": "connector_slug is required"}, status=400)

    api_key = _api_key_or_500()
    if isinstance(api_key, JsonResponse):
        return api_key

    rid, error = _ensure_registered_user_remote(user=user, app_slug=app.slug, api_key=api_key)
    if error is not None:
        logger.error("merge connector-status: ensure failed: %s", error)
        return JsonResponse({"error": "merge ensure failed"}, status=502)

    connected, conn_error = _authenticated_connectors(api_key=api_key, registered_user_id=rid)
    if conn_error is not None:
        logger.error("merge connector-status connections failed: %s", conn_error)
        return JsonResponse({"error": "merge connections failed"}, status=502)
    return JsonResponse({
        "slug": connector_slug,
        "status": "connected" if connector_slug in connected else "not_connected",
    })


@csrf_exempt
@require_POST
def integrations_merge_disconnect(request: HttpRequest) -> JsonResponse:
    """Revoke one connector for this registered user."""
    resolved = _resolve_caller(request=request)
    if isinstance(resolved, JsonResponse):
        return resolved
    app, user = resolved

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)
    connector_slug = payload.get("connector_slug", "")
    if not isinstance(connector_slug, str) or not connector_slug:
        return JsonResponse({"error": "connector_slug is required"}, status=400)

    api_key = _api_key_or_500()
    if isinstance(api_key, JsonResponse):
        return api_key

    rid, error = _ensure_registered_user_remote(user=user, app_slug=app.slug, api_key=api_key)
    if error is not None:
        logger.error("merge disconnect: ensure failed: %s", error)
        return JsonResponse({"error": "merge ensure failed"}, status=502)

    try:
        response = httpx.delete(
            f"{MERGE_API_BASE}/api/v1/credentials/registered-users/{rid}/connectors/{connector_slug}/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=MERGE_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("merge disconnect network failure: %s", exc)
        return JsonResponse({"error": "merge upstream error"}, status=502)
    if response.status_code not in (200, 204, 404):
        logger.error("merge disconnect http %d: %s", response.status_code, response.text[:300])
        return JsonResponse({"error": "merge disconnect failed"}, status=502)
    return JsonResponse({"ok": True})


@csrf_exempt
def integrations_merge_mcp(request: HttpRequest) -> StreamingHttpResponse | JsonResponse:
    """Streaming-HTTP MCP relay. Sandbox → aggregator → here → Merge.

    Forwards POST (JSON-RPC), GET (SSE listening stream), DELETE (session
    terminate) verbatim — the three client-side methods MCP Streamable HTTP
    defines. POST-only would push fastmcp into its 405-fallback path and
    emit two GET retries + one DELETE per session as DOH WARN log noise.

    DOH derives tool_pack_id and registered_user_id from authenticated state;
    the broker has no way to influence which Merge target this hits.
    """
    if request.method not in ("POST", "GET", "DELETE"):
        return JsonResponse({"error": "method not allowed"}, status=405)
    resolved = _resolve_caller(request=request)
    if isinstance(resolved, JsonResponse):
        return resolved
    app, user = resolved

    api_key = _api_key_or_500()
    if isinstance(api_key, JsonResponse):
        return api_key
    pack_id = _tool_pack_or_500()
    if isinstance(pack_id, JsonResponse):
        return pack_id

    rid, error = _ensure_registered_user_remote(user=user, app_slug=app.slug, api_key=api_key)
    if error is not None:
        logger.error("merge mcp: ensure failed: %s", error)
        return JsonResponse({"error": "merge ensure failed"}, status=502)

    # `authenticated_only=true` asks Merge to omit tools for connectors the
    # user hasn't authenticated yet, plus the per-connector `authenticate_*`
    # meta-tools. Combined with the broker's per-backend reload on connect,
    # the agent's catalog mirrors what's actually callable. The full firehose
    # ballooned the prompt past the context window with ~1500 tool defs.
    upstream_url = f"{MERGE_API_BASE}/api/v1/tool-packs/{pack_id}/registered-users/{rid}/mcp/?authenticated_only=true"
    # Forward MCP Streamable HTTP correlation headers (session/protocol/
    # last-event-id) when set, so DELETE terminates the right session and
    # SSE GETs can resume by Last-Event-Id.
    upstream_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": request.headers.get("Content-Type", "application/json"),
        "Accept": request.headers.get("Accept", "application/json, text/event-stream"),
        **{h: request.headers[h] for h in ("Mcp-Session-Id", "Mcp-Protocol-Version", "Last-Event-Id") if h in request.headers},
    }
    timeout = httpx.Timeout(
        connect=MERGE_MCP_CONNECT_TIMEOUT_SECONDS,
        read=MERGE_MCP_READ_TIMEOUT_SECONDS,
        write=MERGE_MCP_CONNECT_TIMEOUT_SECONDS,
        pool=MERGE_MCP_CONNECT_TIMEOUT_SECONDS,
    )

    # Open the upstream connection eagerly to capture status + response headers
    # before returning the StreamingHttpResponse.
    client = httpx.Client(timeout=timeout)
    try:
        upstream = client.send(
            request=client.build_request(
                method=request.method, url=upstream_url, headers=upstream_headers, content=request.body,
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        client.close()
        logger.error("merge mcp upstream %s failed: %s", request.method, exc)
        return JsonResponse({"error": "merge upstream error"}, status=502)

    def iter_and_close():
        try:
            for chunk in upstream.iter_bytes():
                if chunk:
                    yield chunk
        finally:
            upstream.close()
            client.close()

    streaming = StreamingHttpResponse(
        streaming_content=iter_and_close(),
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json"),
    )
    # Forward MCP-relevant headers; drop hop-by-hop ones.
    for header_name in ("Cache-Control", "Mcp-Session-Id"):
        if header_name in upstream.headers:
            streaming[header_name] = upstream.headers[header_name]
    return streaming
