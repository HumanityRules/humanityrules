"""TLS-intercept proxy for platform-managed provider credentials — the subsystem's front door.

The sandbox gets HTTPS_PROXY pointed at this proxy and SSL_CERT_FILE pointed at
our CA bundle, so every outbound HTTPS request arrives here as a CONNECT. A host
no provider claims falls through to an opaque tunnel — the agent reaches the rest
of the internet without this proxy reading it. A host in the catalog gets
terminated with a minted leaf cert, its credential swapped for the real secret,
and each request replayed upstream.

That flow is this file. Each part it composes is one module:

- `tls_provider_catalog` — which hosts are intercepted and how each provider's
  credential works. Static data; the only file a new provider needs.
- `tls_certificate_authority` — the CA and the per-host leaf certs that let us
  terminate TLS as the upstream.
- `tls_token_store` — the real secrets and cache-independent connection state
  for a provider slug, refreshed from HUMR.
- `tls_credential_injection` — whether a request is asking for HUMR's credential,
  and where the secret is written into it.
- `tls_http_message_relay` — provider-agnostic HTTP/1.1: parse, frame, replay
  upstream, stream the response back.

Pure mechanism: cache invalidation and refresh do not perform credential-change
choreography. The env re-render, process restarts, and auth-marker updates that
follow a credential change live in `credentials_service`, which calls down into
this runtime — never the other way around.
"""

import asyncio
import contextlib
import logging
import ssl
from pathlib import Path

from humr_client import HumrClient
import tls_certificate_authority
import tls_credential_injection
import tls_http_message_relay
import tls_provider_catalog
import tls_token_store


logger = logging.getLogger("tls_intercept")


REFRESH_LEAD_SECONDS = 300

# Public browser-facing status strings owned by the TLS-intercept subsystem contract.
STATUS_CONNECTED = "connected"
STATUS_NOT_CONNECTED = "not_connected"


def _status_item_for_provider(
    provider: tls_provider_catalog.TlsProviderSpec,
    state: tls_token_store.ProviderConnectionState | None,
) -> dict:
    """Join a static provider spec with its dynamic state for the integrations payload."""
    method = provider.credential_method
    is_connected = state is not None and state.connected
    return {
        "kind": "tls_intercept",
        "category": provider.category,
        "slug": provider.slug,
        "label": provider.label,
        "logo_url": provider.logo_url,
        "status": STATUS_CONNECTED if is_connected else STATUS_NOT_CONNECTED,
        "last_refreshed_at": state.last_refreshed_at if is_connected else None,
        "config": state.config if is_connected else {},
        "metadata": state.metadata if is_connected else {},
        "connect_mode": method.connect_mode,
        "restart_required_after_save": provider.restart_gateway_after_save or provider.restart_webui_after_save,
        "affects_model_picker": provider.affects_model_picker,
    }


class TlsInterceptRuntime:
    """TLS-intercept subsystem: proxy transport, token refresh, and status cards."""

    def __init__(
        self,
        providers: dict[str, tls_provider_catalog.TlsProviderSpec],
        humr_client: HumrClient,
        refresh_lead_seconds: int,
        ca_dir: Path,
        private_dir: Path,
    ) -> None:
        self._providers = dict(providers)
        self._host_to_provider = tls_provider_catalog.build_host_to_provider(providers=self._providers)
        self._credential_state_store = tls_token_store.CredentialStateStore(
            provider_slugs=tuple(self._providers),
            humr_client=humr_client,
            refresh_lead_seconds=refresh_lead_seconds,
        )
        self._cert_minter = tls_certificate_authority.CertMinter(ca_dir=ca_dir, private_dir=private_dir)
        self._cert_minter.bootstrap()

    async def start_proxy_server(self, host: str, port: int) -> asyncio.Server:
        """Start the local HTTPS proxy server."""
        async def handle_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await _handle_proxy_conn(
                reader=reader,
                writer=writer,
                minter=self._cert_minter,
                providers=self._providers,
                host_to_provider=self._host_to_provider,
                credential_state_store=self._credential_state_store,
            )

        return await asyncio.start_server(client_connected_cb=handle_conn, host=host, port=port)

    async def status_items(self) -> list[dict]:
        """Return TLS-intercept integration cards."""
        connection_states = await self._credential_state_store.connection_snapshot()
        return [
            _status_item_for_provider(provider=provider, state=connection_states.get(provider.slug))
            for provider in self._providers.values()
        ]

    async def connected_slugs(self) -> frozenset[str]:
        """Return the providers whose cache-independent connection state is connected."""
        connection_states = await self._credential_state_store.connection_snapshot()
        return frozenset(slug for slug, state in connection_states.items() if state.connected)

    async def invalidate(self, slug: str) -> None:
        """Drop one provider's cached token entry."""
        await self._credential_state_store.invalidate(slug=slug)

    async def invalidate_all(self) -> None:
        """Drop every cached token entry."""
        await self._credential_state_store.invalidate_all()

    async def mark_disconnected(self, slug: str) -> None:
        """Mark a provider disconnected after a confirmed disconnect (drops cache + flips connection state)."""
        await self._credential_state_store.mark_disconnected(slug=slug)

    async def refresh_slug(self, slug: str) -> bool:
        """Force a single-provider refetch; False when the HUMR round-trip failed transiently."""
        return await self._credential_state_store.refresh(slug=slug)

    async def refresh_all(self) -> bool:
        """Force a refetch of every provider in one HUMR round-trip; False on transient failure."""
        return await self._credential_state_store.refresh_all()

    async def gateway_env_snapshot(self) -> list[tuple[tls_provider_catalog.TlsProviderSpec, dict]]:
        """Pair every connected provider with its last-known config for env rendering."""
        connection_states = await self._credential_state_store.connection_snapshot()
        snapshot: list[tuple[tls_provider_catalog.TlsProviderSpec, dict]] = []
        for provider in self._providers.values():
            state = connection_states.get(provider.slug)
            if state is not None and state.connected:
                snapshot.append((provider, state.config))
        return snapshot


async def _handle_proxy_conn(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    minter: tls_certificate_authority.CertMinter,
    providers: dict[str, tls_provider_catalog.TlsProviderSpec],
    host_to_provider: dict[str, str],
    credential_state_store: tls_token_store.CredentialStateStore,
) -> None:
    """Accept a CONNECT, then either intercept known hosts or tunnel."""
    peer = writer.get_extra_info("peername")
    try:
        request_line = await reader.readline()
        if not request_line:
            return
        try:
            method, target, _ = request_line.decode("iso-8859-1").strip().split(" ", 2)
        except ValueError:
            await tls_http_message_relay.send_raw(writer=writer, status=400, body=b"bad request line")
            return
        while True:
            header_line = await reader.readline()
            if header_line in (b"\r\n", b"\n", b""):
                break
        if method.upper() != "CONNECT":
            await tls_http_message_relay.send_raw(writer=writer, status=405, body=b"only CONNECT is supported")
            return
        raw_host, _, port_str = target.partition(":")
        host = tls_provider_catalog.normalize_connect_host(host=raw_host)
        port = int(port_str) if port_str else 443
        provider_slug = host_to_provider.get(host)
        if provider_slug is None:
            await _tunnel_opaque(client_reader=reader, client_writer=writer, host=host, port=port)
            return
        provider = providers[provider_slug]
        await _intercept_and_forward(
            client_reader=reader,
            client_writer=writer,
            host=host,
            port=port,
            provider=provider,
            minter=minter,
            credential_state_store=credential_state_store,
        )
    except (ConnectionResetError, BrokenPipeError):
        return
    except Exception:
        logger.exception("proxy connection failed (peer=%s)", peer)
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def _tunnel_opaque(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, host: str, port: int) -> None:
    """Straight CONNECT tunnel for hosts we do not intercept."""
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(host=host, port=port)
    except OSError as exc:
        await tls_http_message_relay.send_raw(writer=client_writer, status=502, body=f"upstream connect failed: {exc}".encode())
        return
    client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await client_writer.drain()
    await tls_http_message_relay.pump_both_ways(a_reader=client_reader, a_writer=client_writer, b_reader=upstream_reader, b_writer=upstream_writer)


async def _intercept_and_forward(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    host: str,
    port: int,
    provider: tls_provider_catalog.TlsProviderSpec,
    minter: tls_certificate_authority.CertMinter,
    credential_state_store: tls_token_store.CredentialStateStore,
) -> None:
    """TLS-terminate with a minted leaf, swap Authorization, and forward."""
    client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await client_writer.drain()
    ssl_ctx = minter.context_for(hostname=host)
    loop = asyncio.get_running_loop()
    transport = client_writer.transport
    protocol = transport.get_protocol()
    try:
        new_transport = await loop.start_tls(
            transport=transport,
            protocol=protocol,
            sslcontext=ssl_ctx,
            server_side=True,
        )
    except ssl.SSLError as exc:
        logger.error("TLS handshake with sandbox client failed for %s: %s", host, exc)
        return
    tls_reader = client_reader
    tls_writer = asyncio.StreamWriter(transport=new_transport, protocol=protocol, reader=tls_reader, loop=loop)
    try:
        while True:
            request_line = await tls_reader.readline()
            if not request_line:
                return
            headers_raw: list[bytes] = []
            while True:
                line = await tls_reader.readline()
                headers_raw.append(line)
                if line in (b"\r\n", b"\n", b""):
                    break
            headers = tls_http_message_relay.parse_headers(lines=headers_raw)
            path_with_query = request_line.decode("iso-8859-1").split(" ", 2)[1]
            try:
                body = await tls_http_message_relay.read_body(reader=tls_reader, headers=headers)
            except ValueError as exc:
                # Unparseable framing: whatever follows on the socket can't
                # be delimited, so answer 400 and close rather than read
                # body bytes as the next request line.
                await tls_http_message_relay.send_json_error(writer=tls_writer, status=400, message=f"bad request framing: {exc}")
                return
            try:
                injected_humr_credential = tls_credential_injection.needs_injection(
                    headers=headers,
                    path_with_query=path_with_query,
                    provider=provider,
                )
            except tls_credential_injection.SecretSelectionError as exc:
                logger.error("%s secret selection failed: %s", provider.slug, exc)
                await tls_http_message_relay.send_json_error(
                    writer=tls_writer,
                    status=400,
                    message=f"{provider.slug}: {exc}",
                )
                return
            if injected_humr_credential:
                secrets = await credential_state_store.secrets_for_slug(slug=provider.slug)
                if secrets is None:
                    await _send_provider_not_connected(writer=tls_writer, provider=provider)
                    return
                try:
                    forward_headers, forward_path = tls_credential_injection.rewrite_request_for_provider(
                        headers=headers,
                        path_with_query=path_with_query,
                        secrets=secrets,
                        provider=provider,
                    )
                except tls_credential_injection.SecretSelectionError as exc:
                    logger.error("%s secret selection failed: %s", provider.slug, exc)
                    await tls_http_message_relay.send_json_error(
                        writer=tls_writer,
                        status=400,
                        message=f"{provider.slug}: {exc}",
                    )
                    return
            else:
                forward_headers = headers
                forward_path = path_with_query
            try:
                upstream_status, keep_alive = await tls_http_message_relay.forward_to_upstream(
                    host=host,
                    port=port,
                    method=request_line.decode("iso-8859-1").split(" ", 1)[0],
                    path_with_query=forward_path,
                    headers=forward_headers,
                    body=body,
                    client_writer=tls_writer,
                )
            except Exception as exc:
                logger.exception("forward to %s failed", host)
                await tls_http_message_relay.send_json_error(writer=tls_writer, status=502, message=f"broker upstream error: {exc}")
                return
            # Treat upstream 401 as "the cached token is no longer valid":
            # evict it so the next request refetches from HUMR. Covers both
            # transient-after-rotation and user-revoked-on-provider-side.
            # We don't retry within this connection — the user's next
            # request through the proxy hits the refreshed token. Anonymous
            # pass-through 401s must not evict: they never used our token,
            # so the cached entry is not implicated.
            if upstream_status == 401 and injected_humr_credential:
                await credential_state_store.invalidate(slug=provider.slug)
                logger.info("evicted %s token cache after upstream 401 from %s", provider.slug, host)
            if not keep_alive:
                return
            # The client asked to close after this exchange; don't sit in
            # readline() waiting for a request that will never come.
            if tls_http_message_relay.connection_close_requested(headers=headers):
                return
    finally:
        with contextlib.suppress(Exception):
            tls_writer.close()
            await tls_writer.wait_closed()


async def _send_provider_not_connected(writer: asyncio.StreamWriter, provider: tls_provider_catalog.TlsProviderSpec) -> None:
    """Return a Google-API-shaped not-connected error to the sandbox client."""
    await tls_http_message_relay.send_json_error(
        writer=writer,
        status=503,
        message=f"{provider.slug} integration not connected in HUMR — connect it from the Integrations pane.",
    )
