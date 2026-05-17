"""Shared helpers for DCR connectors. Keep small."""

import logging
from dataclasses import dataclass
from typing import Callable


logger = logging.getLogger("connectors")


# Read/write verb tables for mutation classification of upstream tool names.
# Used by per-connector heuristics to default `mutates` correctly when the
# upstream MCP doesn't tell us which tools mutate state.
READ_VERBS = frozenset({"list", "get", "search", "retrieve", "fetch", "read", "find", "describe", "show", "query", "validate"})
WRITE_VERBS = frozenset({"create", "update", "delete", "post", "send", "patch", "put", "remove", "merge", "close", "open", "archive"})


def mutates_from_dash_name(*, name: str) -> bool:
    """Heuristic for kebab-case tool names like `notion-get-page` or `feature-flag-update`.

    Default to mutates on ambiguity — surfacing a confirmation prompt to the
    user is much cheaper than silently mutating state when the verb is unclear.
    """
    parts = name.lower().split("-")
    for token in parts:
        if token in READ_VERBS:
            return False
        if token in WRITE_VERBS:
            return True
    return True


@dataclass(frozen=True)
class DCRConnectorSpec:
    """Static config + factory for one DCR-OAuth connector.

    `make_backend(oauth_state, refresh_fn, persistent_dir, on_config_change) -> Backend`
    returns the Backend instance the catalog store will consult. `oauth_state`
    and `refresh_fn` are owned by the aggregator and passed through;
    `persistent_dir` is the connector's own subdir for any extra state (e.g.
    PostHog's per-session config); `on_config_change` is the aggregator's
    catalog-reload callback the backend fires after a state change.

    `connect_kind` surfaces in the `not_connected` error returned by
    integrations_call_tool so the LLM/UX dispatcher knows which connect flow to
    point the user at. All current DCR connectors share `oauth_dcr_pkce`; Merge
    is `magic_link` and lives outside this spec.
    """
    slug: str
    label: str
    upstream_url: str
    oauth_metadata_url: str
    default_scope: str | None  # None when the AS ignores scope (Notion).
    make_backend: Callable  # (oauth_state, refresh_fn, persistent_dir, on_config_change) -> Backend
    connect_kind: str
