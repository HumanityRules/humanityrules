"""Front door of the TLS-intercept subsystem: the local HTTPS proxy that stands
between the sandboxed agent and the outside world.

The agent cannot hold real credentials. When it calls a managed API — Gmail,
GitHub, an LLM provider, and so on — the call still looks like ordinary HTTPS
from inside the sandbox. What actually happens is that every outbound HTTPS
request is forced through this proxy (`HTTPS_PROXY` points here; the sandbox
trusts a CA bundle we control via `SSL_CERT_FILE`). The proxy is the place
where a real credential can be written into the request, because it lives
outside the sandbox and already holds the user's secrets in memory.

Think of an arriving connection in two stages.

First the sandbox asks the proxy to open a tunnel to some hostname (an HTTP
`CONNECT`). The proxy looks that hostname up in the provider catalog:

- If no provider lists it, the proxy becomes a dumb pipe. Bytes flow both
  ways untouched; the agent has a normal end-to-end TLS session with whoever
  it called. That is how the rest of the internet keeps working.
- If a provider does list it, the proxy does *not* become a pipe. It answers
  the tunnel request, then pretends to *be* that hostname for the sandbox's
  TLS handshake — presenting a certificate it minted for the occasion. From
  the sandbox's point of view the connection looks legitimate. From the
  proxy's point of view the HTTP inside is now readable.

Second, for each HTTP request on that readable connection, the proxy:

1. Decides whether this request needs a managed credential (and rejects it
   cleanly if the sandbox sent something the provider's rules forbid).
2. Looks up the real secrets for that provider, refreshing from HUMR when
   the cache is stale or empty.
3. Rewrites the request so the secret is where the upstream expects it.
4. Refuses the request with a 402 when it would be billed to an organization
   whose credits are exhausted. Only metered requests are refused: traffic
   that costs no credits — connectors, and model calls on the customer's own
   credential — is never blocked, because blocking it would break workflows
   for no economic reason.
5. Opens a fresh TLS connection to the real provider, sends the rewritten
   request, and streams the response back — optionally watching the body
   for billable usage when HUMR itself funded the credential.
6. If the provider answers 401 on a request we injected into, drops the
   cached secret so the next call refetches. A 401 on anonymous traffic
   never touches the cache; we did not put our secret in that request.

`TlsInterceptRuntime` is the object the rest of the broker holds. It owns
host routing, the status cards and gateway-env snapshots (joining each
static catalog entry with live connection state), and the proxy lifecycle
above. The pieces it composes each do one job and nothing else:

- `tls_provider_catalog` — static per-provider declarations (hosts, wire
  behavior, env bindings, connect UX).
- `tls_certificate_authority` — the CA and the per-host certificates that
  let us terminate the sandbox's TLS.
- `tls_credential_state` — the in-memory secrets and "is this provider
  connected?" state, refreshed from HUMR.
- `tls_credential_injection` — turn a sandbox request + a wire behavior
  into a concrete rewrite plan, then apply it once secrets are in hand.
- `tls_http_message_relay` — provider-agnostic HTTP/1.1 read/write/stream.
- `billing_service` — the single billing decision for each request: refuse,
  attach a usage tap, or forward untouched. Absent billing, every request is
  forwarded without a tap.

This runtime only manages its own in-memory credential cache. The broader
choreography after a connect or disconnect — re-rendering gateway env,
restarting processes, updating auth markers — lives in
`credentials_service`, which calls down into this runtime and is never
called back.
"""

import asyncio
import contextlib
import logging
import ssl
from pathlib import Path

import billing_service as billing
from humr_client import HumrClient
import tls_certificate_authority
import tls_credential_injection
import tls_credential_state
import tls_http_message_relay
import tls_provider_catalog


logger = logging.getLogger("tls_intercept")


REFRESH_LEAD_SECONDS = 300

# Public browser-facing status strings owned by the TLS-intercept subsystem contract.
STATUS_CONNECTED = "connected"
STATUS_NOT_CONNECTED = "not_connected"


class _ProviderNotConnected(Exception):
    """The requested provider has no credential available in HUMR."""


def _status_item_for_provider(
    provider: tls_provider_catalog.TlsProviderSpec,
    state: tls_credential_state.ProviderConnectionState | None,
) -> dict:
    """Join a static provider spec with its dynamic state for the integrations payload."""
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
        "connect_mode": provider.connect_mode,
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
        billing_service: billing.BillingService | None,
    ) -> None:
        self._billing_service = billing_service
        self._providers = dict(providers)
        self._host_to_provider = tls_provider_catalog.build_host_to_provider(providers=self._providers)
        self._credential_state_store = tls_credential_state.CredentialStateStore(
            provider_slugs=tuple(self._providers),
            humr_client=humr_client,
            refresh_lead_seconds=refresh_lead_seconds,
        )
        self._cert_minter = tls_certificate_authority.CertMinter(ca_dir=ca_dir, private_dir=private_dir)
        self._cert_minter.bootstrap()

    async def start_proxy_server(self, host: str, port: int) -> asyncio.Server:
        """Start the local HTTPS proxy server."""
        async def handle_connection(sandbox_reader: asyncio.StreamReader, sandbox_writer: asyncio.StreamWriter) -> None:
            await _handle_proxy_connection(
                sandbox_reader=sandbox_reader,
                sandbox_writer=sandbox_writer,
                minter=self._cert_minter,
                providers=self._providers,
                host_to_provider=self._host_to_provider,
                credential_state_store=self._credential_state_store,
                billing_service=self._billing_service,
            )

        return await asyncio.start_server(client_connected_cb=handle_connection, host=host, port=port)

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

    async def drop_cached_token(self, slug: str) -> None:
        """Drop one provider's cached token entry."""
        await self._credential_state_store.drop_cached_token(slug=slug)

    async def drop_all_cached_tokens(self) -> None:
        """Drop every cached token entry."""
        await self._credential_state_store.drop_all_cached_tokens()

    async def mark_disconnected(self, slug: str) -> None:
        """Mark a provider disconnected after a confirmed disconnect (drops cache + flips connection state)."""
        await self._credential_state_store.mark_disconnected(slug=slug)

    async def refresh(self, slug: str) -> bool:
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


async def _handle_proxy_connection(
    sandbox_reader: asyncio.StreamReader,
    sandbox_writer: asyncio.StreamWriter,
    minter: tls_certificate_authority.CertMinter,
    providers: dict[str, tls_provider_catalog.TlsProviderSpec],
    host_to_provider: dict[str, str],
    credential_state_store: tls_credential_state.CredentialStateStore,
    billing_service: billing.BillingService | None,
) -> None:
    """Accept a CONNECT, then either intercept known hosts or tunnel."""
    peer = sandbox_writer.get_extra_info("peername")
    try:
        request_line = await sandbox_reader.readline()
        if not request_line:
            return
        try:
            method, target, _ = request_line.decode("iso-8859-1").strip().split(" ", 2)
        except ValueError:
            await tls_http_message_relay.send_raw(writer=sandbox_writer, status=400, body=b"bad request line")
            return
        while True:
            header_line = await sandbox_reader.readline()
            if header_line in (b"\r\n", b"\n", b""):
                break
        if method.upper() != "CONNECT":
            await tls_http_message_relay.send_raw(writer=sandbox_writer, status=405, body=b"only CONNECT is supported")
            return
        raw_host, _, port_str = target.partition(":")
        host = tls_provider_catalog.normalize_connect_host(host=raw_host)
        port = int(port_str) if port_str else 443
        provider_slug = host_to_provider.get(host)
        if provider_slug is None:
            await _tunnel_opaque(
                sandbox_reader=sandbox_reader,
                sandbox_writer=sandbox_writer,
                destination_host=host,
                destination_port=port,
            )
            return
        provider = providers[provider_slug]
        await _serve_intercepted_connection(
            sandbox_reader=sandbox_reader,
            sandbox_writer=sandbox_writer,
            provider_host=host,
            provider_port=port,
            provider=provider,
            minter=minter,
            credential_state_store=credential_state_store,
            billing_service=billing_service,
        )
    except (ConnectionResetError, BrokenPipeError):
        return
    except Exception:
        logger.exception("proxy connection failed (peer=%s)", peer)
    finally:
        with contextlib.suppress(Exception):
            sandbox_writer.close()
            await sandbox_writer.wait_closed()


async def _serve_intercepted_connection(
    sandbox_reader: asyncio.StreamReader,
    sandbox_writer: asyncio.StreamWriter,
    provider_host: str,
    provider_port: int,
    provider: tls_provider_catalog.TlsProviderSpec,
    minter: tls_certificate_authority.CertMinter,
    credential_state_store: tls_credential_state.CredentialStateStore,
    billing_service: billing.BillingService | None,
) -> None:
    """Terminate sandbox TLS and forward its HTTP requests to one provider."""
    sandbox_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await sandbox_writer.drain()
    ssl_context = minter.context_for(hostname=provider_host)
    loop = asyncio.get_running_loop()
    transport = sandbox_writer.transport
    protocol = transport.get_protocol()
    try:
        new_transport = await loop.start_tls(
            transport=transport,
            protocol=protocol,
            sslcontext=ssl_context,
            server_side=True,
        )
    except ssl.SSLError as exc:
        logger.error("TLS handshake with sandbox client failed for %s: %s", provider_host, exc)
        return
    sandbox_tls_reader = sandbox_reader
    sandbox_tls_writer = asyncio.StreamWriter(
        transport=new_transport,
        protocol=protocol,
        reader=sandbox_tls_reader,
        loop=loop,
    )
    try:
        while True:
            try:
                sandbox_request = await tls_http_message_relay.read_sandbox_request(sandbox_reader=sandbox_tls_reader)
            except ValueError as exc:
                await tls_http_message_relay.send_json_error(
                    writer=sandbox_tls_writer,
                    status=400,
                    message=f"bad request: {exc}",
                )
                return
            if sandbox_request is None:
                return

            try:
                provider_request, injected_credential = await _build_provider_request(
                    sandbox_request=sandbox_request,
                    provider=provider,
                    credential_state_store=credential_state_store,
                )
            except tls_credential_injection.SecretSelectionError as exc:
                logger.error("%s secret selection failed: %s", provider.slug, exc)
                await tls_http_message_relay.send_json_error(
                    writer=sandbox_tls_writer,
                    status=400,
                    message=f"{provider.slug}: {exc}",
                )
                return
            except tls_credential_injection.CredentialContractError as exc:
                logger.error("%s credential contract failed: %s", provider.slug, exc)
                await tls_http_message_relay.send_json_error(
                    writer=sandbox_tls_writer,
                    status=502,
                    message=f"{provider.slug}: HUMR returned an incomplete managed credential",
                )
                return
            except _ProviderNotConnected:
                await _send_provider_not_connected(sandbox_writer=sandbox_tls_writer, provider=provider)
                return

            # Scheduled and background work reaches this same billing boundary
            # as an interactive chat turn, with no special case.
            billing_decision = await _billing_decision(
                billing_service=billing_service,
                provider_slug=provider.slug,
                platform_shared=injected_credential is not None and injected_credential.platform_shared,
            )
            if billing_decision.refusal is not None:
                logger.info("refused %s request: organization credits exhausted", provider.slug)
                await tls_http_message_relay.send_json_response(
                    writer=sandbox_tls_writer,
                    status=402,
                    payload=billing_decision.refusal,
                )
                return

            usage_tap = billing_decision.usage_tap

            if usage_tap is not None:
                # Drop Accept-Encoding so the provider can't compress (usage stays readable).
                # Observe-only past this — a tap failure logs "unmetered"; the relay never notices.
                provider_request = tls_http_message_relay.ProviderRequest(
                    method=provider_request.method,
                    path_with_query=provider_request.path_with_query,
                    headers=[(name, value) for name, value in provider_request.headers if name.lower() != b"accept-encoding"],
                    body=provider_request.body,
                )

            try:
                forward_result = await tls_http_message_relay.forward_to_provider(
                    provider_host=provider_host,
                    provider_port=provider_port,
                    request=provider_request,
                    sandbox_writer=sandbox_tls_writer,
                    response_body_observer=usage_tap,
                )
            except Exception as exc:
                logger.exception("forward to %s failed", provider_host)
                await tls_http_message_relay.send_json_error(
                    writer=sandbox_tls_writer,
                    status=502,
                    message=f"broker provider error: {exc}",
                )
                return

            # Treat provider 401 as "the cached token is no longer valid": evict it so the next request refetches from
            # HUMR. Covers both transient-after-rotation and user-revoked-on-provider-side. We don't retry within this
            # connection — the user's next request through the proxy hits the refreshed token. A pass-through 401 must
            # not evict: the broker did not place its cached credential in that request.
            if forward_result.status_code == 401 and injected_credential is not None:
                await credential_state_store.drop_cached_token(slug=provider.slug)
                logger.info("evicted %s token cache after provider 401 from %s", provider.slug, provider_host)

            if not forward_result.sandbox_connection_can_continue:
                return

            # The sandbox asked to close after this request; don't sit in
            # readline() waiting for a request that will never come.
            if tls_http_message_relay.connection_close_requested(headers=sandbox_request.headers):
                return
    finally:
        with contextlib.suppress(Exception):
            sandbox_tls_writer.close()
            await sandbox_tls_writer.wait_closed()


async def _billing_decision(
    billing_service: billing.BillingService | None,
    provider_slug: str,
    platform_shared: bool,
) -> billing.BillingDecision:
    """Ask billing once, failing open because billing is an overlay on the proxy path."""
    if billing_service is None:
        return billing.BillingDecision(refusal=None, usage_tap=None)
    try:
        billing_decision = await billing_service.decision_for_request(
            provider_slug=provider_slug,
            platform_shared=platform_shared,
        )
        if not isinstance(billing_decision, billing.BillingDecision):
            raise TypeError("billing service returned an invalid decision")
        return billing_decision
    except Exception:
        logger.exception("%s: billing decision failed; letting the request through", provider_slug)
        return billing.BillingDecision(refusal=None, usage_tap=None)


async def _build_provider_request(
    sandbox_request: tls_http_message_relay.SandboxRequest,
    provider: tls_provider_catalog.TlsProviderSpec,
    credential_state_store: tls_credential_state.CredentialStateStore,
) -> tuple[tls_http_message_relay.ProviderRequest, tls_credential_state.ActiveCredential | None]:
    """
    Build a provider request from the sandbox request.

    Injects credentials when the provider's wire behavior requires them and
    returns the credential it injected; otherwise returns the sandbox request
    unchanged with None (passthrough).
    """
    injection_plan = tls_credential_injection.plan_injection(
        headers=sandbox_request.headers,
        path_with_query=sandbox_request.path_with_query,
        behavior=provider.credential_wire_behavior,
    )
    if injection_plan is None:
        return (
            tls_http_message_relay.ProviderRequest(
                method=sandbox_request.method,
                path_with_query=sandbox_request.path_with_query,
                headers=sandbox_request.headers,
                body=sandbox_request.body,
            ),
            None,
        )

    credential = await credential_state_store.credential_for_slug(slug=provider.slug)
    if credential is None:
        raise _ProviderNotConnected

    provider_headers, provider_path = tls_credential_injection.apply_injection_plan(
        headers=sandbox_request.headers,
        path_with_query=sandbox_request.path_with_query,
        secrets=credential.secrets,
        plan=injection_plan,
    )

    return (
        tls_http_message_relay.ProviderRequest(
            method=sandbox_request.method,
            path_with_query=provider_path,
            headers=provider_headers,
            body=sandbox_request.body,
        ),
        credential,
    )


async def _tunnel_opaque(
    sandbox_reader: asyncio.StreamReader,
    sandbox_writer: asyncio.StreamWriter,
    destination_host: str,
    destination_port: int,
) -> None:
    """Straight CONNECT tunnel for hosts we do not intercept."""
    try:
        destination_reader, destination_writer = await asyncio.open_connection(host=destination_host, port=destination_port)
    except OSError as exc:
        await tls_http_message_relay.send_raw(writer=sandbox_writer, status=502, body=f"destination connect failed: {exc}".encode())
        return
    sandbox_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await sandbox_writer.drain()
    await tls_http_message_relay.pump_both_ways(
        sandbox_reader=sandbox_reader,
        sandbox_writer=sandbox_writer,
        destination_reader=destination_reader,
        destination_writer=destination_writer,
    )


async def _send_provider_not_connected(sandbox_writer: asyncio.StreamWriter, provider: tls_provider_catalog.TlsProviderSpec) -> None:
    """Tell the sandbox that the requested provider is not connected."""
    await tls_http_message_relay.send_json_error(
        writer=sandbox_writer,
        status=503,
        message=f"{provider.slug} integration not connected in HUMR — connect it from the Integrations pane.",
    )
