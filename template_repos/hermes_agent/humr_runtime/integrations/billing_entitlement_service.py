"""What HUMR says this organization may still spend, cached at the broker.

The control plane owns the balance. The broker owns one cached copy of the
answer, and the two decisions that follow from it: whether to refuse a model
call, and what the WebUI's credits card should show. Both read the same
snapshot, so enforcement and display cannot disagree.

Refresh is event-driven; there is no timer anywhere in here.

1. Every accepted usage-event post answers with a fresh snapshot, so an
   organization that is actively spending stays current for free.
2. In the exhausted state the cache is deliberately not trusted: each metered
   request re-asks HUMR before being refused. Exhaustion is the one state where
   a stale answer is expensive, because an organization that just upgraded must
   be unblocked on its next turn rather than its next flush.
3. The card's `GET /__humr_broker/billing` refreshes lazily once the copy is
   over a minute old, which is what keeps an idle organization's card honest.

Failure semantics follow from what enforcement is for. While HUMR runs on a
flat-cost inference subscription, blocking protects customer expectations
rather than margin, so the failure that costs nothing is the one to prefer: with
no snapshot at all the broker never blocks, and an exhausted organization whose
control plane is unreachable keeps being refused on the last thing HUMR actually
said rather than being silently let through.

Refusal targets exactly the requests that would have been metered — the
predicate lives in `tls_usage_metering.request_is_metered` and is asked by both
sides. Customer-funded model traffic and every connector call consume no
credits, so exhaustion never touches them.
"""

import asyncio
import logging
import time

from humr_client import HumrClient


logger = logging.getLogger("billing_entitlement_service")


ENTITLEMENT_PATH = "/api/runtime/billing-entitlement"

# Where a user goes to fix an exhausted balance. Named in the 402 the agent
# reads out loud, and handed to the credits card so it need not know HUMR's URL
# shape.
BILLING_PAGE_PATH = "/settings/billing/"

# How old the cached copy may be before the card's GET refreshes it first.
_DISPLAY_STALE_SECONDS = 60

_FETCH_TIMEOUT_SECONDS = 10

def _is_count(value: object) -> bool:
    """True for an int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_entitlement_snapshot(body: object) -> dict | None:
    """Read the entitlement snapshot out of a control-plane body, or None if it carries none.

    Both HUMR endpoints answer with the snapshot under `entitlement`, so this is
    the only parser either refresh trigger needs. A body that does not carry a
    complete, well-typed snapshot is not a snapshot: the cache keeps whatever it
    already had rather than half-adopting one.
    """
    if not isinstance(body, dict):
        return None
    raw = body.get("entitlement")
    if not isinstance(raw, dict):
        return None
    credits_remaining = raw.get("credits_remaining")
    monthly_grant = raw.get("monthly_grant")
    renewal_date = raw.get("renewal_date")
    plan = raw.get("plan")
    exhausted = raw.get("exhausted")
    if not _is_count(credits_remaining) or not _is_count(monthly_grant):
        return None
    if renewal_date is not None and not isinstance(renewal_date, str):
        return None
    if not isinstance(plan, str) or not isinstance(exhausted, bool):
        return None
    return {
        "credits_remaining": credits_remaining,
        "monthly_grant": monthly_grant,
        "renewal_date": renewal_date,
        "plan": plan,
        "exhausted": exhausted,
    }


def _refusal_body(entitlement_snapshot: dict, upgrade_url: str) -> dict:
    """The 402 payload a refused model call gets back.

    The agent has no billing UI of its own; it relays whatever the provider
    said. So `error.message` is written for a person reading it in chat, and it
    names the one or two things that actually resolve the block. The renewal
    sentence appears only for plans that renew — a trial's grant is one-time,
    and telling a trial user to wait would be a lie.
    """
    renewal_date = entitlement_snapshot["renewal_date"]
    if renewal_date:
        resolution = f"Upgrade the plan at {upgrade_url}, or wait for credits to renew on {renewal_date}."
    else:
        resolution = f"Upgrade the plan at {upgrade_url} to keep going."
    return {
        "error": {
            "type": "insufficient_credits",
            "code": "credits_exhausted",
            "message": f"This Humanity Rules organization is out of credits, so the model call was not sent. {resolution}",
        },
        "entitlement": entitlement_snapshot,
        "upgrade_url": upgrade_url,
    }


class BillingEntitlementService:
    """The broker's authority on whether the organization may still spend.

    Its working state is one cached snapshot of HUMR's answer, replaced on the
    event-driven triggers described in the module docstring.
    """

    def __init__(self, humr_client: HumrClient) -> None:
        self._humr_client = humr_client
        self._entitlement_snapshot: dict | None = None
        self._fetched_at: float | None = None
        # Bumped on every replacement. A caller samples it before waiting on the
        # fetch lock, so a burst of concurrent metered requests collapses into
        # one HUMR round-trip instead of one each.
        self._generation = 0
        self._fetch_lock = asyncio.Lock()

    def upgrade_url(self) -> str:
        """HUMR's billing page for this deployment's control plane."""
        return f"{self._humr_client.control_plane_url}{BILLING_PAGE_PATH}"

    def absorb_report_response(self, body: object) -> None:
        """Take the snapshot riding back on an accepted usage-event post."""
        entitlement_snapshot = _parse_entitlement_snapshot(body=body)
        if entitlement_snapshot is None:
            logger.error("usage report response carried no usable entitlement snapshot; keeping last-known state")
            return
        self._replace(entitlement_snapshot=entitlement_snapshot)

    async def entitlement_snapshot_for_display(self) -> dict | None:
        """The snapshot the credits card renders, refreshed first when it has gone stale.

        Also the path that gives an idle organization a card at all: one that has
        never spent has never posted a usage event, so this is its first fetch.
        """
        if self._fetched_at is None or time.monotonic() - self._fetched_at >= _DISPLAY_STALE_SECONDS:
            await self._refresh(generation=self._generation)
        return self._entitlement_snapshot

    async def refusal_for_metered_request(self) -> dict | None:
        """The 402 body for one metered request, or None to let it through."""
        entitlement_snapshot = self._entitlement_snapshot
        if entitlement_snapshot is None:
            return None  # HUMR has never answered; never block on a guess
        if not entitlement_snapshot["exhausted"]:
            return None  # the ordinary path costs nothing
        # Exhausted: re-ask rather than trust the copy, so an upgrade or a
        # renewal unblocks this very request. A failed refresh leaves the
        # last-known "no" in place.
        await self._refresh(generation=self._generation)
        entitlement_snapshot = self._entitlement_snapshot
        if entitlement_snapshot is None or not entitlement_snapshot["exhausted"]:
            return None
        return _refusal_body(entitlement_snapshot=entitlement_snapshot, upgrade_url=self.upgrade_url())

    def _replace(self, entitlement_snapshot: dict) -> None:
        self._entitlement_snapshot = entitlement_snapshot
        self._fetched_at = time.monotonic()
        self._generation += 1

    async def _refresh(self, generation: int) -> None:
        """Pull a fresh snapshot from HUMR; keep the last-known state on any failure."""
        async with self._fetch_lock:
            if self._generation != generation:
                return  # someone else refreshed while this caller waited
            status, body = await self._humr_client.get_json(
                path=ENTITLEMENT_PATH,
                timeout_seconds=_FETCH_TIMEOUT_SECONDS,
            )
            if not (200 <= status < 300):
                logger.error("entitlement snapshot refresh got HTTP %d; keeping last-known state", status)
                return
            entitlement_snapshot = _parse_entitlement_snapshot(body=body)
            if entitlement_snapshot is None:
                logger.error("entitlement snapshot refresh carried no usable entitlement snapshot; keeping last-known state")
                return
            self._replace(entitlement_snapshot=entitlement_snapshot)
