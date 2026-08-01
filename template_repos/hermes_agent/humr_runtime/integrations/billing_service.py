"""The broker's single billing boundary.

The rest of the broker asks one question per provider request and receives one
`BillingDecision`: refuse with a structured 402 body, observe the decoded
response through a usage tap, or forward untouched. This module owns the policy
that decides which requests consume HUMR credits, while the entitlement member
owns cached control-plane state and the metering member owns response parsing
and report delivery.

The reporter's accepted-response callback feeds the entitlement member, so an
actively spending organization refreshes its enforcement state without another
control-plane request. The facade also exposes the reporter's long-running loop
and the two reads needed by the control API; neither internal object crosses
this boundary.
"""

import logging
from dataclasses import dataclass

import billing_entitlement
import billing_usage_metering
import humr_client


logger = logging.getLogger("billing_service")


_PARSED_PROVIDER_SLUGS = frozenset({"openai-codex"})


def _request_is_metered(provider_slug: str, platform_shared: bool) -> bool:
    """Whether HUMR funds this request and can parse its response usage."""
    return platform_shared and provider_slug in _PARSED_PROVIDER_SLUGS


@dataclass(frozen=True)
class BillingDecision:
    """The one billing action the intercept applies to a provider request."""

    refusal: dict | None
    usage_tap: billing_usage_metering.UsageTap | None

    def __post_init__(self) -> None:
        if self.refusal is not None and self.usage_tap is not None:
            raise ValueError("a billing decision cannot both refuse and meter a request")


FORWARD_UNTOUCHED = BillingDecision(refusal=None, usage_tap=None)


class BillingService:
    """Compose billing policy, entitlement state, metering, and delivery."""

    def __init__(self, humr_client: humr_client.HumrClient) -> None:
        self._entitlement = billing_entitlement.BillingEntitlement(humr_client=humr_client)
        self._usage_reporter = billing_usage_metering.UsageReporter(
            humr_client=humr_client,
            on_report_response=self._entitlement.absorb_report_response,
        )

    async def decision_for_request(self, provider_slug: str, platform_shared: bool) -> BillingDecision:
        """Return the billing action without raising; internal failures log and forward untouched.

        Billing is an overlay on the proxy path, so a billing failure must not
        prevent the request from reaching its provider.
        """
        try:
            if not _request_is_metered(provider_slug=provider_slug, platform_shared=platform_shared):
                return FORWARD_UNTOUCHED
            refusal = await self._entitlement.refusal_for_metered_request()
            if refusal is not None:
                return BillingDecision(refusal=refusal, usage_tap=None)
            usage_tap = billing_usage_metering.UsageTap(
                provider_slug=provider_slug,
                record_usage=self._usage_reporter.record,
            )
            return BillingDecision(refusal=None, usage_tap=usage_tap)
        except Exception:
            logger.exception("%s: billing decision failed; forwarding request untouched", provider_slug)
            return FORWARD_UNTOUCHED

    async def run_usage_flush_loop(self) -> None:
        """Run the usage reporter's flush loop."""
        await self._usage_reporter.run()

    async def entitlement_snapshot_for_display(self) -> dict | None:
        """Return the entitlement snapshot the credits card should display."""
        return await self._entitlement.entitlement_snapshot_for_display()

    def upgrade_url(self) -> str:
        """Return the control-plane billing page for this deployment."""
        return self._entitlement.upgrade_url()
