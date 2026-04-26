"""
Mock PDP used by the policy-proxy end-to-end test.

Replaces DOH's /api/pdp/evaluate with a deterministic "does this username appear
in ALLOWED_USERNAMES?" check so the test doesn't need the real DOH control
plane to be reachable from the customer VPC.

Triggered by ALB (ALB -> Lambda event shape). One route:
- POST /evaluate  body: {"app_id","oidc_sub","username","path"} -> {"decision","reason"}

Allow/deny is decided purely from the ALLOWED_USERNAMES env var (comma-separated).
Anything about the real ABAC engine lives in devopshero_app.services.abac and is
tested separately; this lambda intentionally stays trivial.
"""

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _allowed_usernames() -> set[str]:
    raw = os.environ.get("ALLOWED_USERNAMES", "")
    return {u.strip() for u in raw.split(",") if u.strip()}


def _alb_response(status_code: int, body: dict, content_type: str = "application/json") -> dict:
    return {
        "statusCode": status_code,
        "isBase64Encoded": False,
        "headers": {"content-type": content_type},
        "body": json.dumps(body),
    }


def handler(event: dict, context) -> dict:
    path = event.get("path", "/")
    method = event.get("httpMethod", "GET")
    logger.info("pdp-mock %s %s", method, path)

    if path != "/evaluate" or method != "POST":
        return _alb_response(404, {"error": "not found"})

    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _alb_response(400, {"error": "invalid JSON body"})

    # Require the shape the real PDP requires, so contract drift fails loud.
    for field in ("app_id", "oidc_sub", "username", "path"):
        if not isinstance(payload.get(field), str):
            return _alb_response(400, {"error": f"missing or non-string '{field}'"})

    username = payload["username"]
    allowed = _allowed_usernames()
    if username in allowed:
        logger.info(
            "pdp-mock allow username=%s app=%s path=%s", username, payload["app_id"], payload["path"],
        )
        return _alb_response(200, {"decision": "allow", "reason": "mock-allowlist"})

    logger.info(
        "pdp-mock deny username=%s app=%s allowed=%s", username, payload["app_id"], sorted(allowed),
    )
    return _alb_response(200, {"decision": "deny", "reason": "mock-not-in-allowlist"})
