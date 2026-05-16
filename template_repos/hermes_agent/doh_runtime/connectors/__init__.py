"""DCR connector registry.

Each module under this package is a self-contained adapter for a third-party
MCP server reached via OAuth Dynamic Client Registration + PKCE. The aggregator
imports `DCR_CONNECTORS` at startup and uses it to:

  1. Drive the OAuth start/callback/refresh handlers (looked up by slug).
  2. Build the `Backend` instance list passed to the catalog store.

To add a connector: write `connectors/<slug>.py` exporting `SPEC: DCRConnectorSpec`,
then append it to `DCR_CONNECTORS` below. Keep the connector module self-
contained — its data model, mutation overrides, scope strings, and any synthetic
config tools all live there. Repeat code across connectors before reaching for
abstractions; PostHog and Notion share little, and that's fine.

Merge is NOT in this list — it uses a Magic Link flow through DOH's relay, not
DCR/PKCE. See `MergeBackend` in `mcp_merge_backend.py`.
"""

from ._common import DCRConnectorSpec
from . import notion as _notion
from . import posthog as _posthog


DCR_CONNECTORS: list[DCRConnectorSpec] = [
    _notion.SPEC,
    _posthog.SPEC,
]


__all__ = ["DCRConnectorSpec", "DCR_CONNECTORS"]
