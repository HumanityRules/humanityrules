"""Swapping in the real secret on an intercepted request.

Two steps: `needs_injection` decides whether the request is ours to touch, and
`rewrite_request_for_provider` writes the secret in wherever that provider
wants it. Both take their rules from the provider's `credential_method` in
`tls_provider_catalog`.

Neither touches the network or the token cache — the caller looks the secrets
up and passes them in. Supporting a new credential method is a dataclass in
`tls_provider_catalog` plus one more branch in each function here.
"""

import base64

import tls_http_message_relay
import tls_provider_catalog


def _primary_secret(secrets: dict[str, str]) -> str:
    """Return the sole secret for a single-secret provider.

    Single-secret methods (OAuthHeader, VaultUrlRewrite) carry exactly one
    secret, so the primary is unambiguous. Multi-secret providers (Slack)
    select per request in `rewrite_request_for_provider` and don't use this.
    """
    return next(iter(secrets.values()))


class SecretSelectionError(Exception):
    """A request didn't carry a recognizable placeholder, or a required secret is missing from the cache.

    Raised by both `needs_injection` and `rewrite_request_for_provider`; the
    proxy loop maps it to a 400 so an un-rewritten credential is never
    forwarded upstream.
    """


def _build_authorization_value(token: str, auth_format: str) -> bytes:
    """Encode the upstream Authorization header for a given provider's auth format."""
    if auth_format == tls_provider_catalog.AUTH_FORMAT_BEARER:
        return b"Bearer " + token.encode()
    if auth_format == tls_provider_catalog.AUTH_FORMAT_BASIC_X_ACCESS_TOKEN:
        creds = b"x-access-token:" + token.encode()
        return b"Basic " + base64.b64encode(creds)
    raise ValueError(f"unknown auth_format: {auth_format!r}")


def _strip_bearer_prefix(value: bytes) -> str:
    """Return the token from a `Bearer <token>` header value (case-insensitive prefix)."""
    text = value.decode("iso-8859-1").strip()
    if text[:7].lower() == "bearer ":
        return text[7:].strip()
    return text


def _rewrite_authorization(headers: list[tuple[bytes, bytes]], token: str, auth_format: str, upstream_host: str) -> list[tuple[bytes, bytes]]:
    auth_value = _build_authorization_value(token=token, auth_format=auth_format)
    host_override = upstream_host.encode()
    rewritten: list[tuple[bytes, bytes]] = []
    seen_auth = False
    for name, value in headers:
        if name == b"authorization":
            rewritten.append((b"Authorization", auth_value))
            seen_auth = True
            continue
        if name == b"host":
            rewritten.append((b"Host", host_override))
            continue
        if name in (b"proxy-connection", b"proxy-authorization"):
            continue
        rewritten.append((name, value))
    if not seen_auth:
        rewritten.append((b"Authorization", auth_value))
    return rewritten


def _inject_headers(headers: list[tuple[bytes, bytes]], extra: dict[bytes, bytes]) -> list[tuple[bytes, bytes]]:
    """Force `extra` header values, replacing any the client sent (case-insensitive).

    Used after `_rewrite_authorization` to add broker-owned headers (e.g.
    `ChatGPT-Account-ID`) whose values come from HUMR, not the sandbox. A header
    the sandbox sent under the same name is dropped so the sandbox can't spoof
    it; every other client header (Codex's Cloudflare `originator`/`User-Agent`)
    is left as-is.
    """
    lowered = {name.lower() for name in extra}
    kept = [(name, value) for name, value in headers if name.lower() not in lowered]
    return kept + list(extra.items())


def needs_injection(headers: list[tuple[bytes, bytes]], path_with_query: str, provider: tls_provider_catalog.TlsProviderSpec) -> bool:
    """Decide whether HUMR's credential must be injected into this request, or it is anonymous public traffic.

    True routes through the token store + rewrite path. OAuth-style methods
    (OAuthHeader, OAuthHeaderMultiInject) are always True: their convention is
    inverted — the sandbox sends no marker and the proxy injects
    unconditionally, so every request implicitly asks for HUMR's credential.

    Vault-style methods mark HUMR's slot with an explicit placeholder. False
    means every credential slot is empty — anonymous public traffic (e.g.
    OpenRouter's unauthenticated /api/v1/models) the proxy forwards as-is,
    without consulting the token store, so a disconnected provider does not
    cost one HUMR refresh per request. A credential that is neither empty nor
    a recognized placeholder raises `SecretSelectionError` (→ 400): BYO keys
    are neither injected-over nor silently forwarded.
    """
    method = provider.credential_method
    if isinstance(method, (tls_provider_catalog.OAuthHeader, tls_provider_catalog.OAuthHeaderMultiInject)):
        return True
    if isinstance(method, tls_provider_catalog.VaultUrlRewrite):
        # Every Telegram Bot API path embeds a token, so this host has no
        # anonymous surface: a path without the placeholder carries an
        # un-rewritable credential, never public traffic.
        if method.placeholder not in path_with_query:
            raise SecretSelectionError("request URL must contain the HUMR placeholder")
        return True
    if isinstance(method, tls_provider_catalog.VaultHeaderInject):
        incoming = next((v for n, v in headers if n.lower() == b"authorization"), None)
        if incoming is None:
            return False
        if method.secret_for_placeholder(_strip_bearer_prefix(incoming)) is None:
            raise SecretSelectionError("request Authorization did not carry a known HUMR placeholder")
        return True
    if isinstance(method, tls_provider_catalog.VaultApiKeyHeader):
        header_lower = method.header_name.lower().encode()
        incoming = next((v for n, v in headers if n.lower() == header_lower), None)
        if incoming is not None:
            if incoming.decode("iso-8859-1").strip() != method.placeholder:
                raise SecretSelectionError(f"request {method.header_name} did not carry the HUMR placeholder")
            return True
        # No api-key slot, but an Authorization header (e.g. a BYO OAuth
        # bearer) still counts as credentialed — refuse rather than forward.
        if any(n.lower() == b"authorization" for n, _v in headers):
            raise SecretSelectionError(f"request carried Authorization instead of the {method.header_name} HUMR placeholder")
        return False
    raise ValueError(f"unknown credential_method: {method!r}")


def rewrite_request_for_provider(
    headers: list[tuple[bytes, bytes]],
    path_with_query: str,
    secrets: dict[str, str],
    provider: tls_provider_catalog.TlsProviderSpec,
    upstream_host: str,
) -> tuple[list[tuple[bytes, bytes]], str]:
    """Rewrite credentials for the provider-specific upstream API shape.

    Single-secret methods (OAuthHeader, VaultUrlRewrite) use the sole secret.
    OAuthHeaderMultiInject swaps the bearer and injects its extra header(s)
    from named secrets, leaving other client headers intact. VaultHeaderInject
    selects per request by reverse-mapping the incoming placeholder bearer to
    its secret name. Raises `SecretSelectionError` when a required secret is
    missing or the request doesn't carry a recognizable placeholder.
    """
    method = provider.credential_method
    if isinstance(method, tls_provider_catalog.OAuthHeader):
        return (
            _rewrite_authorization(
                headers=headers,
                token=_primary_secret(secrets),
                auth_format=method.auth_format,
                upstream_host=upstream_host,
            ),
            path_with_query,
        )
    if isinstance(method, tls_provider_catalog.OAuthHeaderMultiInject):
        bearer = secrets.get(method.bearer_secret)
        if not bearer:
            raise SecretSelectionError(f"no cached secret for {method.bearer_secret!r}")
        rewritten = _rewrite_authorization(
            headers=headers,
            token=bearer,
            auth_format=method.auth_format,
            upstream_host=upstream_host,
        )
        extra: dict[bytes, bytes] = {}
        for secret_name, header_name in method.header_secrets.items():
            value = secrets.get(secret_name)
            if not value:
                raise SecretSelectionError(f"no cached secret for {secret_name!r}")
            extra[header_name.encode()] = value.encode()
        return (_inject_headers(headers=rewritten, extra=extra), path_with_query)
    if isinstance(method, tls_provider_catalog.VaultUrlRewrite):
        token = _primary_secret(secrets)
        if method.placeholder not in path_with_query:
            raise SecretSelectionError("request URL must contain the HUMR placeholder")
        return (
            tls_http_message_relay.strip_proxy_headers_and_set_host(headers=headers, upstream_host=upstream_host),
            path_with_query.replace(method.placeholder, token),
        )
    if isinstance(method, tls_provider_catalog.VaultHeaderInject):
        # Read the raw (case-preserving) Authorization value — the relay's
        # header lookup lowercases, which would mangle a mixed-case
        # placeholder token.
        incoming = next((v for n, v in headers if n.lower() == b"authorization"), None)
        bearer = _strip_bearer_prefix(incoming) if incoming is not None else None
        secret_name = method.secret_for_placeholder(bearer) if bearer is not None else None
        if secret_name is None:
            raise SecretSelectionError("request Authorization did not carry a known HUMR placeholder")
        token = secrets.get(secret_name)
        if not token:
            raise SecretSelectionError(f"no cached secret for {secret_name!r}")
        return (
            _rewrite_authorization(
                headers=headers,
                token=token,
                auth_format=method.auth_format,
                upstream_host=upstream_host,
            ),
            path_with_query,
        )
    if isinstance(method, tls_provider_catalog.VaultApiKeyHeader):
        # Confirm the request carries our placeholder in the named auth header
        # (case-preserving read), then swap in the real key. Other headers — incl.
        # Anthropic's required anthropic-version — pass through untouched.
        header_lower = method.header_name.lower().encode()
        incoming = next((v for n, v in headers if n.lower() == header_lower), None)
        incoming_value = incoming.decode("iso-8859-1").strip() if incoming is not None else None
        if incoming_value != method.placeholder:
            raise SecretSelectionError(f"request {method.header_name} did not carry the HUMR placeholder")
        token = _primary_secret(secrets)
        stripped = tls_http_message_relay.strip_proxy_headers_and_set_host(headers=headers, upstream_host=upstream_host)
        return (
            _inject_headers(headers=stripped, extra={method.header_name.encode(): token.encode()}),
            path_with_query,
        )
    raise ValueError(f"unknown credential_method: {method!r}")
