"""Decide how — and whether — a managed credential should enter one intercepted
request, then perform that rewrite once the secrets are in hand.

The TLS-intercept proxy has already opened the sandbox's HTTPS and can read
the request. What it still needs to know is provider-specific and
request-specific: does this call want HUMR's managed credential at all? If
so, which named secret(s), and where on the wire do they go — a header, a
URL path segment, both? Different providers answer differently. Google gets
a bearer token on every call. Telegram embeds the bot token in the URL path
and expects a placeholder there. Slack and some LLM providers send a
placeholder header that selects which secret to use, and an empty slot means
"forward anonymously."

This module is that decision, split into two deliberate steps so the policy
never waits on the network:

1. `plan_injection` looks only at the sandbox request and the provider's
   `credential_wire_behavior` from the catalog. It returns an
   `InjectionPlan` naming the secrets and destinations, `None` for anonymous
   pass-through, or raises `SecretSelectionError` when the request is
   refused (a foreign credential, a missing URL placeholder, Authorization
   used where a custom placeholder header was required, and so on). No
   token lookup happens here — a refused request never costs a HUMR call.

2. `apply_injection_plan` runs after the caller has fetched the named
   secrets. It performs only the substitutions the plan recorded: write
   header values in the right encoding, replace a URL placeholder, strip
   Authorization when the plan says so. It does not re-branch on provider
   behavior. A secret the plan required but HUMR did not return raises
   `CredentialContractError` — that is a control-plane contract failure,
   not a sandbox request error.

Neither function opens a socket or reads the token cache. Framing, Host
rewrites, and everything else about HTTP transport stay in
`tls_http_message_relay`. The three wire-behavior types themselves
(`AlwaysInjectHeaders`, `HeaderPlaceholder`, `UrlCredentialPlaceholder`)
are declared in `tls_provider_catalog`; each has exactly one planning
branch here.
"""

import base64
from dataclasses import dataclass

import tls_provider_catalog


@dataclass(frozen=True)
class InjectionPlan:
    """A request-specific description of how managed secrets enter the request."""

    header_injections: tuple[tls_provider_catalog.HeaderInjection, ...]
    url_credential_placeholder: tls_provider_catalog.UrlCredentialPlaceholder | None
    remove_authorization: bool


class SecretSelectionError(Exception):
    """The sandbox request does not satisfy its credential wire behavior."""


class CredentialContractError(Exception):
    """The control plane did not return a secret required by the provider catalog."""


def _build_header_value(secret: str, header_value_format: tls_provider_catalog.HeaderValueFormat) -> bytes:
    """Encode one secret for its provider request header."""
    if header_value_format == tls_provider_catalog.HEADER_VALUE_RAW:
        return secret.encode()
    if header_value_format == tls_provider_catalog.HEADER_VALUE_BEARER:
        return b"Bearer " + secret.encode()
    if header_value_format == tls_provider_catalog.HEADER_VALUE_BASIC_X_ACCESS_TOKEN:
        credentials = b"x-access-token:" + secret.encode()
        return b"Basic " + base64.b64encode(credentials)
    raise ValueError(f"unknown header value format: {header_value_format!r}")


def _strip_bearer_prefix(value: bytes) -> str:
    """Return the token from a `Bearer <token>` value, preserving the current lenient parsing."""
    text = value.decode("iso-8859-1").strip()
    if text[:7].lower() == "bearer ":
        return text[7:].strip()
    return text


def _remove_headers(headers: list[tuple[bytes, bytes]], header_names: set[bytes]) -> list[tuple[bytes, bytes]]:
    """Remove every occurrence of the named headers case-insensitively."""
    lowered_names = {name.lower() for name in header_names}
    return [(name, value) for name, value in headers if name.lower() not in lowered_names]


def _inject_headers(headers: list[tuple[bytes, bytes]], replacements: dict[bytes, bytes]) -> list[tuple[bytes, bytes]]:
    """Replace named client headers with broker-owned values."""
    kept_headers = _remove_headers(headers=headers, header_names=set(replacements))
    return kept_headers + list(replacements.items())


def _incoming_placeholder_value(value: bytes, header_value_format: tls_provider_catalog.PlaceholderHeaderValueFormat) -> str:
    """Extract the configured placeholder value from an incoming header."""
    if header_value_format == tls_provider_catalog.HEADER_VALUE_BEARER:
        return _strip_bearer_prefix(value=value)
    return value.decode("iso-8859-1").strip()


def plan_injection(
    headers: list[tuple[bytes, bytes]],
    path_with_query: str,
    behavior: tls_provider_catalog.CredentialWireBehavior,
) -> InjectionPlan | None:
    """Return the managed-credential plan, `None` for pass-through, or reject the request."""
    if isinstance(behavior, tls_provider_catalog.AlwaysInjectHeaders):
        return InjectionPlan(
            header_injections=behavior.header_injections,
            url_credential_placeholder=None,
            remove_authorization=False,
        )

    if isinstance(behavior, tls_provider_catalog.HeaderPlaceholder):
        header_name = behavior.header_name.encode()
        header_name_lower = header_name.lower()
        incoming = next((value for name, value in headers if name.lower() == header_name_lower), None)
        if incoming is None:
            carries_authorization = any(name.lower() == b"authorization" for name, _value in headers)
            if behavior.reject_authorization_when_placeholder_missing and carries_authorization:
                raise SecretSelectionError(
                    f"request carried Authorization instead of the {behavior.header_name} HUMR placeholder"
                )
            return None

        placeholder_value = _incoming_placeholder_value(
            value=incoming,
            header_value_format=behavior.header_value_format,
        )
        secret_name = behavior.secret_for_placeholder(placeholder_value=placeholder_value)
        if secret_name is None:
            if header_name_lower == b"authorization":
                raise SecretSelectionError("request Authorization did not carry a known HUMR placeholder")
            raise SecretSelectionError(f"request {behavior.header_name} did not carry the HUMR placeholder")

        return InjectionPlan(
            header_injections=(
                tls_provider_catalog.HeaderInjection(
                    header_name=behavior.header_name,
                    secret_name=secret_name,
                    header_value_format=behavior.header_value_format,
                ),
            ),
            url_credential_placeholder=None,
            remove_authorization=behavior.remove_authorization,
        )

    if isinstance(behavior, tls_provider_catalog.UrlCredentialPlaceholder):
        if behavior.placeholder not in path_with_query:
            raise SecretSelectionError("request URL must contain the HUMR placeholder")
        return InjectionPlan(
            header_injections=(),
            url_credential_placeholder=behavior,
            remove_authorization=behavior.remove_authorization,
        )

    raise ValueError(f"unknown credential wire behavior: {behavior!r}")


def _required_secret(secrets: dict[str, str], secret_name: str) -> str:
    """Return one non-empty secret required by an injection plan."""
    secret = secrets.get(secret_name)
    if not secret:
        raise CredentialContractError(f"managed credential is missing required secret {secret_name!r}")
    return secret


def apply_injection_plan(
    headers: list[tuple[bytes, bytes]],
    path_with_query: str,
    secrets: dict[str, str],
    plan: InjectionPlan,
) -> tuple[list[tuple[bytes, bytes]], str]:
    """Apply a recorded plan using the fetched HUMR secrets."""
    provider_headers = headers
    if plan.remove_authorization:
        provider_headers = _remove_headers(
            headers=provider_headers,
            header_names={b"authorization"},
        )

    header_replacements: dict[bytes, bytes] = {}
    for injection in plan.header_injections:
        secret = _required_secret(secrets=secrets, secret_name=injection.secret_name)
        header_replacements[injection.header_name.encode()] = _build_header_value(
            secret=secret,
            header_value_format=injection.header_value_format,
        )
    if header_replacements:
        provider_headers = _inject_headers(
            headers=provider_headers,
            replacements=header_replacements,
        )

    provider_path = path_with_query
    if plan.url_credential_placeholder is not None:
        path_secret = _required_secret(
            secrets=secrets,
            secret_name=plan.url_credential_placeholder.secret_name,
        )
        provider_path = provider_path.replace(plan.url_credential_placeholder.placeholder, path_secret)

    return provider_headers, provider_path
