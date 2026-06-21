"""PDP client — calls HUMR's /api/pdp/evaluate endpoint per request."""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

PDP_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class PdpDecision:
    """The shape HUMR returns from /api/pdp/evaluate."""
    decision: str  # "allow" or "deny"
    reason: str


async def evaluate(
    http_client: httpx.AsyncClient,
    pdp_url: str,
    env_bearer_token: str,
    app_id: str,
    provider: str,
    sub: str,
    username: str,
    path: str,
) -> PdpDecision | None:
    """Call the PDP. Returns None if the call itself fails (treat as deny)."""
    try:
        response = await http_client.post(
            pdp_url,
            headers={"Authorization": f"Bearer {env_bearer_token}"},
            json={
                "app_id": app_id,
                "provider": provider,
                "sub": sub,
                "username": username,
                "path": path,
            },
            timeout=PDP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("pdp call failed: %s", exc)
        return None

    if response.status_code != 200:
        logger.error("pdp non-200 status=%s body=%s", response.status_code, response.text[:200])
        return None

    try:
        body = response.json()
    except ValueError:
        logger.error("pdp invalid JSON body")
        return None

    decision = body.get("decision", "deny")
    reason = body.get("reason", "")
    if decision not in ("allow", "deny"):
        logger.error("pdp unknown decision=%r", decision)
        return None
    return PdpDecision(decision=decision, reason=reason)
