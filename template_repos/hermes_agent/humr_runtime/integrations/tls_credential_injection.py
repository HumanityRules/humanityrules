"""Plan and apply managed credentials to intercepted provider requests.

`plan_injection` makes the request policy decision before the token-store
lookup. A plan records the selected secret names and their wire destinations,
so `apply_injection_plan` only performs the planned substitutions after the
caller fetches those secrets. `None` means anonymous pass-through; an invalid
or foreign credential raises `SecretSelectionError`.

Neither function touches the network or token cache. HTTP transport
normalization (Host, proxy headers, and framing) remains in
`tls_http_message_relay`.
"""

import base64
from dataclasses import dataclass

import tls_provider_catalog


@dataclass(frozen=True)
class PathInjection:
    """Replace one path placeholder with one named HUMR secret."""

    placeholder: str
    secret_name: str


@dataclass(frozen=True)
class InjectionPlan:
    """A validated description of how managed secrets enter one request."""

    header_injections: tuple[tls_provider_catalog.HeaderInjection, ...]
    path_injection: PathInjection | None
    remove_authorization: bool


class SecretSelectionError(Exception):
    """The request or secret set cannot satisfy its credential wire behavior."""


def _build_header_value(secret: str, value_format: str) -> bytes:
    """Encode one secret for its provider request header."""
    if value_format == tls_provider_catalog.HEADER_VALUE_RAW:
        return secret.encode()
    if value_format == tls_provider_catalog.HEADER_VALUE_BEARER:
        return b"Bearer " + secret.encode()
    if value_format == tls_provider_catalog.HEADER_VALUE_BASIC_X_ACCESS_TOKEN:
        credentials = b"x-access-token:" + secret.encode()
        return b"Basic " + base64.b64encode(credentials)
    raise ValueError(f"unknown header value format: {value_format!r}")


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


def _incoming_placeholder_value(value: bytes, value_format: str) -> str:
    """Extract the configured placeholder value from an incoming header."""
    if value_format == tls_provider_catalog.HEADER_VALUE_BEARER:
        return _strip_bearer_prefix(value=value)
    return value.decode("iso-8859-1").strip()


def plan_injection(
    headers: list[tuple[bytes, bytes]],
    path_with_query: str,
    behavior: tls_provider_catalog.CredentialWireBehavior,
) -> InjectionPlan | None:
    """Return the managed-credential plan, `None` for pass-through, or reject the request."""
    if isinstance(behavior, tls_provider_catalog.AlwaysInject):
        return InjectionPlan(
            header_injections=behavior.header_injections,
            path_injection=None,
            remove_authorization=False,
        )

    if isinstance(behavior, tls_provider_catalog.HeaderPlaceholder):
        header_name = behavior.header_name.encode()
        header_name_lower = header_name.lower()
        incoming = next((value for name, value in headers if name.lower() == header_name_lower), None)
        if incoming is None:
            carries_authorization = any(name.lower() == b"authorization" for name, _value in headers)
            if header_name_lower != b"authorization" and carries_authorization:
                raise SecretSelectionError(
                    f"request carried Authorization instead of the {behavior.header_name} HUMR placeholder"
                )
            return None

        placeholder_value = _incoming_placeholder_value(
            value=incoming,
            value_format=behavior.value_format,
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
                    value_format=behavior.value_format,
                ),
            ),
            path_injection=None,
            remove_authorization=header_name_lower != b"authorization",
        )

    if isinstance(behavior, tls_provider_catalog.PathPlaceholder):
        if behavior.placeholder not in path_with_query:
            raise SecretSelectionError("request URL must contain the HUMR placeholder")
        return InjectionPlan(
            header_injections=(),
            path_injection=PathInjection(
                placeholder=behavior.placeholder,
                secret_name=behavior.secret_name,
            ),
            remove_authorization=True,
        )

    raise ValueError(f"unknown credential wire behavior: {behavior!r}")


def _required_secret(secrets: dict[str, str], secret_name: str) -> str:
    """Return one non-empty secret required by an injection plan."""
    secret = secrets.get(secret_name)
    if not secret:
        raise SecretSelectionError(f"no cached secret for {secret_name!r}")
    return secret


def apply_injection_plan(
    headers: list[tuple[bytes, bytes]],
    path_with_query: str,
    secrets: dict[str, str],
    plan: InjectionPlan,
) -> tuple[list[tuple[bytes, bytes]], str]:
    """Apply a previously validated plan using the fetched HUMR secrets."""
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
            value_format=injection.value_format,
        )
    if header_replacements:
        provider_headers = _inject_headers(
            headers=provider_headers,
            replacements=header_replacements,
        )

    provider_path = path_with_query
    if plan.path_injection is not None:
        path_secret = _required_secret(
            secrets=secrets,
            secret_name=plan.path_injection.secret_name,
        )
        provider_path = provider_path.replace(plan.path_injection.placeholder, path_secret)

    return provider_headers, provider_path
