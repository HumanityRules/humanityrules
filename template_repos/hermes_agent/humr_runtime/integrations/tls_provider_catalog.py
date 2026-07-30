"""Provider catalog for the TLS-intercept proxy.

One `TlsProviderSpec` is one third-party API the broker MITMs. It answers:
which CONNECT hosts to claim, how a managed secret is written onto an
intercepted request (`credential_wire_behavior`), and which env vars to
project while connected (`env_bindings`). Connect UX (`connect_mode`) and
panel placement (`category`) live here too.

This file is only that static data — the wire-behavior types, the specs
tuple, and the slug registry. Runtime joins a spec with live connection
state elsewhere.

To add a provider that reuses an existing wire behavior, append a
`TlsProviderSpec` to `TLS_INTERCEPT_PROVIDER_SPECS`. A new wire behavior
also needs one planning branch in `tls_credential_injection`.
"""

from dataclasses import dataclass
from typing import Literal


# Encodings for secret values written into provider request headers.
HEADER_VALUE_RAW = "raw"
HEADER_VALUE_BEARER = "bearer"
HEADER_VALUE_BASIC_X_ACCESS_TOKEN = "basic_x_access_token"
HUMR_PLACEHOLDER_VALUE = "HUMR_PLACEHOLDER"

HeaderValueFormat = Literal["raw", "bearer", "basic_x_access_token"]
PlaceholderHeaderValueFormat = Literal["raw", "bearer"]
ConnectMode = Literal["oauth", "device", "vault"]

# Which integrations-panel section a provider renders under.
Category = Literal["model_provider", "connector"]


@dataclass(frozen=True)
class HeaderInjection:
    """Write one control-plane secret into one provider request header."""

    header_name: str
    secret_name: str
    header_value_format: HeaderValueFormat

    def __post_init__(self) -> None:
        if self.header_value_format not in {
            HEADER_VALUE_RAW,
            HEADER_VALUE_BEARER,
            HEADER_VALUE_BASIC_X_ACCESS_TOKEN,
        }:
            raise ValueError(f"unsupported header value format: {self.header_value_format!r}")


@dataclass(frozen=True)
class AlwaysInjectHeaders:
    """Every request to the provider receives the configured HUMR secrets."""

    header_injections: tuple[HeaderInjection, ...]


@dataclass(frozen=True)
class EnvBinding:
    """One env var rendered into the managed profile env block while connected."""

    env_var: str
    value: str | None = None
    config_key: str | None = None
    list_separator: str | None = None

    def __post_init__(self) -> None:
        has_value = self.value is not None
        has_config_key = self.config_key is not None
        if has_value == has_config_key:
            raise ValueError("EnvBinding must set exactly one of value or config_key")
        if has_value and self.list_separator is not None:
            raise ValueError("EnvBinding with a static value cannot set list_separator")


@dataclass(frozen=True)
class HeaderPlaceholder:
    """A recognized placeholder header selects one HUMR secret; an empty slot passes through."""

    header_name: str
    placeholder_by_secret_name: dict[str, str]
    header_value_format: PlaceholderHeaderValueFormat
    remove_authorization: bool
    reject_authorization_when_placeholder_missing: bool

    def __post_init__(self) -> None:
        if self.header_value_format not in {HEADER_VALUE_RAW, HEADER_VALUE_BEARER}:
            raise ValueError(f"unsupported placeholder header value format: {self.header_value_format!r}")

    def secret_for_placeholder(self, placeholder_value: str) -> str | None:
        """Return the secret selected by an incoming placeholder value."""
        for secret_name, placeholder in self.placeholder_by_secret_name.items():
            if placeholder == placeholder_value:
                return secret_name
        return None


@dataclass(frozen=True)
class UrlCredentialPlaceholder:
    """The request URL must contain the placeholder for one HUMR secret."""

    placeholder: str
    secret_name: str
    remove_authorization: bool


CredentialWireBehavior = AlwaysInjectHeaders | HeaderPlaceholder | UrlCredentialPlaceholder


@dataclass(frozen=True)
class TlsProviderSpec:
    """Static config for one provider whose HTTPS traffic is intercepted."""

    slug: str
    label: str
    hosts: tuple[str, ...]
    logo_url: str
    connect_mode: ConnectMode
    credential_wire_behavior: CredentialWireBehavior
    env_bindings: tuple[EnvBinding, ...]
    sync_auth_marker: bool
    restart_gateway_after_save: bool
    restart_webui_after_save: bool
    category: Category

    @property
    def affects_model_picker(self) -> bool:
        """Whether connecting/disconnecting this provider changes /api/models.

        Coextensive with being a model provider — connecting a model provider
        is precisely what changes the model list, and nothing else does. Derived
        from `category` so the two can't drift.
        """
        return self.category == "model_provider"


TLS_INTERCEPT_PROVIDER_SPECS = (
    TlsProviderSpec(
        slug="google",
        label="Google Workspace",
        hosts=(
            "gmail.googleapis.com",
            "calendar-json.googleapis.com",
            "drive.googleapis.com",
            "docs.googleapis.com",
            "sheets.googleapis.com",
            "people.googleapis.com",
            "www.googleapis.com",
            "oauth2.googleapis.com",
        ),
        logo_url="/extensions/humr/google-workspace.svg",
        connect_mode="oauth",
        credential_wire_behavior=AlwaysInjectHeaders(
            header_injections=(
                HeaderInjection(
                    header_name="Authorization",
                    secret_name="access_token",
                    header_value_format=HEADER_VALUE_BEARER,
                ),
            ),
        ),
        env_bindings=(),
        sync_auth_marker=False,
        restart_gateway_after_save=False,
        restart_webui_after_save=False,
        category="connector",
    ),
    TlsProviderSpec(
        slug="github",
        label="GitHub",
        hosts=(
            # github.com handles git smart-HTTP (clone/push) and OAuth
            # endpoints; api.github.com handles REST (incl. `gh` CLI);
            # codeload.github.com serves archive/tarball downloads after a
            # github.com redirect.
            "github.com",
            "api.github.com",
            "codeload.github.com",
        ),
        logo_url="/extensions/humr/github.svg",
        connect_mode="oauth",
        credential_wire_behavior=AlwaysInjectHeaders(
            header_injections=(
                HeaderInjection(
                    header_name="Authorization",
                    secret_name="access_token",
                    header_value_format=HEADER_VALUE_BASIC_X_ACCESS_TOKEN,
                ),
            ),
        ),
        env_bindings=(
            EnvBinding(env_var="GITHUB_TOKEN", value=HUMR_PLACEHOLDER_VALUE),
        ),
        sync_auth_marker=False,
        restart_gateway_after_save=False,
        restart_webui_after_save=True,
        category="connector",
    ),
    TlsProviderSpec(
        slug="telegram",
        label="Telegram",
        hosts=("api.telegram.org",),
        logo_url="/extensions/humr/telegram.svg",
        connect_mode="vault",
        credential_wire_behavior=UrlCredentialPlaceholder(
            placeholder="000000:HUMR_PLACEHOLDER",
            secret_name="bot_token",
            remove_authorization=True,
        ),
        env_bindings=(
            EnvBinding(env_var="TELEGRAM_BOT_TOKEN", value="000000:HUMR_PLACEHOLDER"),
            EnvBinding(env_var="TELEGRAM_ALLOWED_USERS", config_key="allowed_users", list_separator=","),
        ),
        sync_auth_marker=False,
        restart_gateway_after_save=True,
        restart_webui_after_save=False,
        category="connector",
    ),
    TlsProviderSpec(
        slug="slack",
        label="Slack",
        # Only the Slack Web API (REST) is intercepted. The Socket Mode
        # `wss://` host is not listed here, so it falls through to a plain
        # CONNECT tunnel — it carries only the short-lived ticket from
        # apps.connections.open, not a long-lived token.
        hosts=("slack.com", "www.slack.com"),
        logo_url="/extensions/humr/slack.svg",
        connect_mode="vault",
        credential_wire_behavior=HeaderPlaceholder(
            header_name="Authorization",
            placeholder_by_secret_name={
                "app_token": "xapp-HUMR_PLACEHOLDER",
                "bot_token": "xoxb-HUMR_PLACEHOLDER",
            },
            header_value_format=HEADER_VALUE_BEARER,
            remove_authorization=False,
            reject_authorization_when_placeholder_missing=False,
        ),
        env_bindings=(
            EnvBinding(env_var="SLACK_APP_TOKEN", value="xapp-HUMR_PLACEHOLDER"),
            EnvBinding(env_var="SLACK_BOT_TOKEN", value="xoxb-HUMR_PLACEHOLDER"),
            # The gateway denies users by default. Company-wide mode sets
            # allow_all_users in config (→ SLACK_ALLOW_ALL_USERS=true);
            # personal mode instead sets allowed_users (owner only). Each
            # binding renders only when its config key is present.
            EnvBinding(env_var="SLACK_ALLOW_ALL_USERS", config_key="allow_all_users"),
            EnvBinding(env_var="SLACK_ALLOWED_USERS", config_key="allowed_users", list_separator=","),
            EnvBinding(env_var="SLACK_HOME_CHANNEL", config_key="home_channel"),
        ),
        sync_auth_marker=False,
        restart_gateway_after_save=True,
        restart_webui_after_save=False,
        category="connector",
    ),
    TlsProviderSpec(
        slug="openai-codex",
        label="OpenAI Codex",
        # The ChatGPT backend Codex talks to. api.openai.com is a different
        # surface (rejected for ChatGPT-subscription auth) and is not listed.
        hosts=("chatgpt.com",),
        logo_url="/extensions/humr/openai.svg",
        connect_mode="device",
        credential_wire_behavior=AlwaysInjectHeaders(
            header_injections=(
                HeaderInjection(
                    header_name="Authorization",
                    secret_name="access_token",
                    header_value_format=HEADER_VALUE_BEARER,
                ),
                HeaderInjection(
                    header_name="ChatGPT-Account-ID",
                    secret_name="chatgpt_account_id",
                    header_value_format=HEADER_VALUE_RAW,
                ),
            ),
        ),
        env_bindings=(),
        sync_auth_marker=True,
        restart_gateway_after_save=False,
        restart_webui_after_save=False,
        category="model_provider",
    ),
    TlsProviderSpec(
        slug="nous",
        label="Nous Portal",
        hosts=("inference-api.nousresearch.com",),
        logo_url="/extensions/humr/nous.svg",
        connect_mode="device",
        credential_wire_behavior=AlwaysInjectHeaders(
            header_injections=(
                HeaderInjection(
                    header_name="Authorization",
                    secret_name="access_token",
                    header_value_format=HEADER_VALUE_BEARER,
                ),
            ),
        ),
        env_bindings=(),
        sync_auth_marker=True,
        restart_gateway_after_save=False,
        restart_webui_after_save=False,
        category="model_provider",
    ),
    TlsProviderSpec(
        slug="openrouter",
        label="OpenRouter",
        hosts=("openrouter.ai",),
        logo_url="/extensions/humr/openrouter.svg",
        connect_mode="vault",
        credential_wire_behavior=HeaderPlaceholder(
            header_name="Authorization",
            placeholder_by_secret_name={"api_key": HUMR_PLACEHOLDER_VALUE},
            header_value_format=HEADER_VALUE_BEARER,
            remove_authorization=False,
            reject_authorization_when_placeholder_missing=False,
        ),
        env_bindings=(
            EnvBinding(env_var="OPENROUTER_API_KEY", value=HUMR_PLACEHOLDER_VALUE),
        ),
        sync_auth_marker=False,
        restart_gateway_after_save=True,
        restart_webui_after_save=True,
        category="model_provider",
    ),
    TlsProviderSpec(
        slug="openai-api",
        label="OpenAI API Key",
        # The OpenAI API surface. chatgpt.com (ChatGPT-subscription auth) is the
        # separate Codex provider and is not listed here.
        hosts=("api.openai.com",),
        logo_url="/extensions/humr/openai.svg",
        connect_mode="vault",
        credential_wire_behavior=HeaderPlaceholder(
            header_name="Authorization",
            placeholder_by_secret_name={"api_key": HUMR_PLACEHOLDER_VALUE},
            header_value_format=HEADER_VALUE_BEARER,
            remove_authorization=False,
            reject_authorization_when_placeholder_missing=False,
        ),
        env_bindings=(
            EnvBinding(env_var="OPENAI_API_KEY", value=HUMR_PLACEHOLDER_VALUE),
        ),
        sync_auth_marker=False,
        restart_gateway_after_save=True,
        restart_webui_after_save=True,
        category="model_provider",
    ),
    TlsProviderSpec(
        slug="anthropic",
        label="Anthropic",
        hosts=("api.anthropic.com",),
        logo_url="/extensions/humr/anthropic.svg",
        # Anthropic authenticates with x-api-key, not Authorization: Bearer.
        connect_mode="vault",
        credential_wire_behavior=HeaderPlaceholder(
            header_name="x-api-key",
            placeholder_by_secret_name={"api_key": HUMR_PLACEHOLDER_VALUE},
            header_value_format=HEADER_VALUE_RAW,
            remove_authorization=True,
            reject_authorization_when_placeholder_missing=True,
        ),
        env_bindings=(
            EnvBinding(env_var="ANTHROPIC_API_KEY", value=HUMR_PLACEHOLDER_VALUE),
        ),
        sync_auth_marker=False,
        restart_gateway_after_save=True,
        restart_webui_after_save=True,
        category="model_provider",
    ),
    TlsProviderSpec(
        slug="browseruse",
        label="Browser Use",
        # Browser Use's cloud-browser API surface. The agent's browser_use
        # provider talks to api.browser-use.com; browser-use.com (marketing/UI)
        # is not an API host and is not intercepted.
        hosts=("api.browser-use.com",),
        logo_url="/extensions/humr/browser-use.svg",
        # Browser Use authenticates with the X-Browser-Use-API-Key header, not
        # Authorization: Bearer — same shape as Anthropic's x-api-key.
        connect_mode="vault",
        credential_wire_behavior=HeaderPlaceholder(
            header_name="X-Browser-Use-API-Key",
            placeholder_by_secret_name={"api_key": HUMR_PLACEHOLDER_VALUE},
            header_value_format=HEADER_VALUE_RAW,
            remove_authorization=True,
            reject_authorization_when_placeholder_missing=True,
        ),
        env_bindings=(
            EnvBinding(env_var="BROWSER_USE_API_KEY", value=HUMR_PLACEHOLDER_VALUE),
        ),
        sync_auth_marker=False,
        # The agent reads BROWSER_USE_API_KEY at call time and the browser tool
        # runs in agent turns served by either process (gateway for cron/platform
        # turns, webui for in-process chat), so both must reload to pick up the
        # placeholder. Connector, not a model provider: it doesn't change /api/models.
        restart_gateway_after_save=True,
        restart_webui_after_save=True,
        category="connector",
    ),
    TlsProviderSpec(
        slug="x",
        label="X",
        # api.x.com serves the X API v2 (xurl's `/2/...` endpoints) and the
        # OAuth 2.0 token endpoint. The browser OAuth dance (x.com/i/oauth2/
        # authorize) happens HUMR-side, not from the sandbox, so x.com is not
        # intercepted — only the bearer-carrying API host is.
        hosts=("api.x.com",),
        logo_url="/extensions/humr/x.svg",
        connect_mode="oauth",
        credential_wire_behavior=AlwaysInjectHeaders(
            header_injections=(
                HeaderInjection(
                    header_name="Authorization",
                    secret_name="access_token",
                    header_value_format=HEADER_VALUE_BEARER,
                ),
            ),
        ),
        env_bindings=(),
        sync_auth_marker=False,
        restart_gateway_after_save=False,
        restart_webui_after_save=False,
        category="connector",
    ),
    TlsProviderSpec(
        slug="tavily",
        label="Tavily",
        # Tavily's web-search/extract/crawl API. A single shared key (no
        # per-user OAuth); the agent authenticates with Authorization: Bearer,
        # the same shape as OpenRouter/OpenAI, so the proxy swaps a placeholder
        # bearer for the real key in flight.
        hosts=("api.tavily.com",),
        logo_url="/extensions/humr/tavily.svg",
        connect_mode="vault",
        credential_wire_behavior=HeaderPlaceholder(
            header_name="Authorization",
            placeholder_by_secret_name={"api_key": "tvly-HUMR_PLACEHOLDER"},
            header_value_format=HEADER_VALUE_BEARER,
            remove_authorization=False,
            reject_authorization_when_placeholder_missing=False,
        ),
        env_bindings=(
            EnvBinding(env_var="TAVILY_API_KEY", value="tvly-HUMR_PLACEHOLDER"),
        ),
        sync_auth_marker=False,
        # web_search/web_extract read TAVILY_API_KEY at call time in agent turns
        # served by either process (gateway for cron/platform turns, webui for
        # chat), so both must reload to pick up the placeholder. Connector, not a
        # model provider: it doesn't change /api/models.
        restart_gateway_after_save=True,
        restart_webui_after_save=True,
        category="connector",
    ),
)


def build_provider_registry(provider_specs: tuple[TlsProviderSpec, ...]) -> dict[str, TlsProviderSpec]:
    """Index provider specs by slug and fail fast on duplicate slugs."""
    providers: dict[str, TlsProviderSpec] = {}
    for spec in provider_specs:
        if spec.slug in providers:
            raise RuntimeError(f"duplicate TLS-intercept provider slug: {spec.slug}")
        providers[spec.slug] = spec
    return providers


def normalize_connect_host(host: str) -> str:
    """Canonicalize CONNECT hostnames before provider routing."""
    return host.strip().rstrip(".").lower()


def build_host_to_provider(providers: dict[str, TlsProviderSpec]) -> dict[str, str]:
    """Map intercepted upstream hosts to provider slugs and fail on overlap."""
    host_to_provider: dict[str, str] = {}
    for slug, spec in providers.items():
        for host in spec.hosts:
            normalized_host = normalize_connect_host(host=host)
            existing_slug = host_to_provider.get(normalized_host)
            if existing_slug is not None:
                raise RuntimeError(
                    f"TLS-intercept host {normalized_host!r} is claimed by both {existing_slug!r} and {slug!r}"
                )
            host_to_provider[normalized_host] = slug
    return host_to_provider


TLS_INTERCEPT_PROVIDERS = build_provider_registry(provider_specs=TLS_INTERCEPT_PROVIDER_SPECS)
