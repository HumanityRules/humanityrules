"""Browser-facing relay for the self-referential permissions editor.

Pure transport: each route parses the WebUI's `/__humr_broker/permissions/*`
request, forwards it to DOH's `/api/permissions/*` via `DohClient` (which attaches
the env bearer and the deployment's owner/app identity), and serializes the
reply. No bearer, no authorization decision, and no target `(app, environment)`
ever lives here — DOH resolves all of that from the bearer + identity.

Browser path-params (request_id) and query (request_id, service) are folded into
the JSON payload because every DOH permissions endpoint is POST + body. The
browser-facing verb and the DOH-facing verb need not match.
"""

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from humr_client import DohClient

logger = logging.getLogger("permissions_control")

# AWS resource listing happens synchronously inside several DOH endpoints
# (draft open, refresh-resources, resources), so allow generous headroom.
PERMISSIONS_TIMEOUT_SECONDS = 60


async def _read_json_body(request: Request) -> dict:
    """Parse the request's JSON body, or {} when there is none."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def routes(prefix: str, humr_client: DohClient) -> list[Route]:
    """Build the `/permissions/*` relay routes mounted under the unified control router."""

    async def _relay(path: str, payload: dict) -> Response:
        status, body = await humr_client.post_json(
            path=path, payload=payload, timeout_seconds=PERMISSIONS_TIMEOUT_SECONDS,
        )
        return JSONResponse(content=body, status_code=status)

    async def draft_route(request: Request) -> Response:
        payload = {}
        request_id = request.query_params.get("request_id")
        if request_id:
            payload["request_id"] = request_id
        return await _relay(path="/api/permissions/draft", payload=payload)

    async def statement_route(request: Request) -> Response:
        payload = await _read_json_body(request=request)
        payload["request_id"] = request.path_params["request_id"]
        return await _relay(path="/api/permissions/draft/statement", payload=payload)

    async def description_route(request: Request) -> Response:
        payload = await _read_json_body(request=request)
        payload["request_id"] = request.path_params["request_id"]
        return await _relay(path="/api/permissions/draft/description", payload=payload)

    async def cancel_route(request: Request) -> Response:
        return await _relay(
            path="/api/permissions/draft/cancel",
            payload={"request_id": request.path_params["request_id"]},
        )

    async def apply_route(request: Request) -> Response:
        return await _relay(
            path="/api/permissions/draft/apply",
            payload={"request_id": request.path_params["request_id"]},
        )

    async def refresh_resources_route(request: Request) -> Response:
        return await _relay(
            path="/api/permissions/draft/refresh-resources",
            payload={"request_id": request.path_params["request_id"]},
        )

    async def resources_route(request: Request) -> Response:
        return await _relay(
            path="/api/permissions/resources",
            payload={"service": request.query_params.get("service", "")},
        )

    async def service_catalog_route(request: Request) -> Response:
        return await _relay(path="/api/permissions/service-catalog", payload={})

    return [
        Route(path=f"{prefix}/draft", endpoint=draft_route, methods=["GET"]),
        Route(path=f"{prefix}/draft/{{request_id}}/statement", endpoint=statement_route, methods=["POST"]),
        Route(path=f"{prefix}/draft/{{request_id}}/description", endpoint=description_route, methods=["POST"]),
        Route(path=f"{prefix}/draft/{{request_id}}/cancel", endpoint=cancel_route, methods=["POST"]),
        Route(path=f"{prefix}/draft/{{request_id}}/apply", endpoint=apply_route, methods=["POST"]),
        Route(path=f"{prefix}/draft/{{request_id}}/refresh-resources", endpoint=refresh_resources_route, methods=["POST"]),
        Route(path=f"{prefix}/resources", endpoint=resources_route, methods=["GET"]),
        Route(path=f"{prefix}/service-catalog", endpoint=service_catalog_route, methods=["GET"]),
    ]
