"""TLS-intercept proxy runtime for platform-managed provider tokens.

The provider catalog lives in `tls_providers`; this package composes the
mechanism submodules — token store, cert minter, and MITM proxy — via
`TlsInterceptRuntime`.
"""

import asyncio
from pathlib import Path

from humr_client import HumrClient
import tls_cert_minter
import tls_proxy
import tls_providers
import tls_token_store


REFRESH_LEAD_SECONDS = tls_token_store.REFRESH_LEAD_SECONDS
STATUS_CONNECTED = tls_token_store.STATUS_CONNECTED
STATUS_NOT_CONNECTED = tls_token_store.STATUS_NOT_CONNECTED
REFRESH_OUTCOME_HAS_TOKEN = tls_token_store.REFRESH_OUTCOME_HAS_TOKEN
REFRESH_OUTCOME_ABSENT = tls_token_store.REFRESH_OUTCOME_ABSENT
REFRESH_OUTCOME_TRANSIENT = tls_token_store.REFRESH_OUTCOME_TRANSIENT
RefreshOutcome = tls_token_store.RefreshOutcome
RefreshResult = tls_token_store.RefreshResult
fetch_provider_tokens_batch = tls_token_store.fetch_provider_tokens_batch
_transient_result = tls_token_store._transient_result
_TokenCacheEntry = tls_token_store._TokenCacheEntry
_ConnState = tls_token_store._ConnState
_TokenStore = tls_token_store._TokenStore
_CertMinter = tls_cert_minter._CertMinter
_handle_proxy_conn = tls_proxy._handle_proxy_conn
_rewrite_authorization = tls_proxy._rewrite_authorization
_rewrite_request_for_provider = tls_proxy._rewrite_request_for_provider
_request_addresses_humr_credential = tls_proxy._request_addresses_humr_credential
_SecretSelectionError = tls_proxy._SecretSelectionError
_normalize_forward_headers = tls_proxy._normalize_forward_headers
_read_chunked = tls_proxy._read_chunked
_forward_to_upstream = tls_proxy._forward_to_upstream


class TlsInterceptRuntime:
    """TLS-intercept subsystem: proxy transport, token refresh, and status cards."""

    def __init__(self, providers: dict[str, tls_providers.TlsProviderSpec], humr_client: HumrClient, refresh_lead_seconds: int, ca_dir: Path, private_dir: Path) -> None:
        self._token_store = tls_token_store._TokenStore(
            providers=providers,
            humr_client=humr_client,
            refresh_lead_seconds=refresh_lead_seconds,
        )
        self._cert_minter = tls_cert_minter._CertMinter(ca_dir=ca_dir, private_dir=private_dir)
        self._cert_minter.bootstrap()

    async def start_proxy_server(self, host: str, port: int) -> asyncio.Server:
        """Start the local HTTPS proxy server."""
        async def handle_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await tls_proxy._handle_proxy_conn(
                reader=reader,
                writer=writer,
                minter=self._cert_minter,
                token_store=self._token_store,
            )

        return await asyncio.start_server(client_connected_cb=handle_conn, host=host, port=port)

    async def status_items(self) -> list[dict]:
        """Return TLS-intercept integration cards."""
        return await self._token_store.status_items()

    async def invalidate(self, slug: str) -> None:
        """Drop one provider's cached token entry."""
        await self._token_store.invalidate(slug=slug)

    async def invalidate_all(self) -> None:
        """Drop every cached token entry."""
        await self._token_store.invalidate_all()

    async def mark_disconnected(self, slug: str) -> None:
        """Mark a provider disconnected after a confirmed disconnect (drops cache + flips connection state)."""
        await self._token_store.mark_disconnected(slug=slug)

    async def refresh_slug(self, slug: str) -> bool:
        """Force a single-provider refetch; False when the HUMR round-trip failed transiently."""
        return await self._token_store.refresh(slug=slug)

    async def refresh_all(self) -> bool:
        """Force a refetch of every provider in one HUMR round-trip; False on transient failure."""
        return await self._token_store.refresh_all()

    async def gateway_env_snapshot(self) -> list[tuple[tls_providers.TlsProviderSpec, dict]]:
        """Pair every connected provider with its last-known config (durable connection state) for env rendering."""
        return await self._token_store.gateway_env_snapshot()
