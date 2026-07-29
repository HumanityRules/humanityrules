"""Provider catalog for the TLS-intercept proxy.

Static data only: the credential-method dataclasses, the per-provider
`TlsProviderSpec` entries, and the registries built from them. The mechanism
that consumes this catalog is one module per part — `tls_intercept` (the
connection lifecycle that composes them), `tls_certificate_authority`,
`tls_token_store`, `tls_credential_injection`, `tls_http_message_relay`; the
gateway-env projection of `env_bindings` lives in `credentials_service`.

To add a provider that uses an existing credential method, append a
`TlsProviderSpec` to `TLS_INTERCEPT_PROVIDER_SPECS` — no mechanism change
needed. A new credential method also needs `needs_injection` and
`rewrite_request_for_provider` branches in `tls_credential_injection`.
"""

from dataclasses import dataclass
from typing import ClassVar, Literal


# Authorization header encodings used by OAuthHeader providers.
AUTH_FORMAT_BEARER = "bearer"
AUTH_FORMAT_BASIC_X_ACCESS_TOKEN = "basic_x_access_token"
HUMR_PLACEHOLDER_VALUE = "HUMR_PLACEHOLDER"
ConnectMode = Literal["oauth", "device", "vault"]
# Which integrations-panel section a provider renders under.
Category = Literal["model_provider", "connector"]


@dataclass(frozen=True)
class OAuthHeader:
    """OAuth integration whose token is injected as the Authorization header.

    `auth_format` selects the header encoding: Google takes plain Bearer;
    GitHub git-smart-HTTP needs HTTP Basic with the token as the password
    under the `x-access-token` username. Redirect OAuth is the default connect
    mode; device-flow providers override it explicitly.
    """

    auth_format: str
    connect_mode: ConnectMode = "oauth"


@dataclass(frozen=True)
class OAuthHeaderMultiInject:
    """OAuth integration whose refresh returns several secrets: one bearer + extra headers.

    Like `OAuthHeader`, the credential rides request headers and needs no
    restart on connect — but HUMR returns more than one secret. `bearer_secret`
    names the one carried as `Authorization: Bearer`; `header_secrets` maps each
    remaining secret name to the HTTP header it's injected as.

    Codex is the consumer: HUMR mints an `access_token` (the bearer) and derives
    `chatgpt_account_id` (the `ChatGPT-Account-ID` header) from it, and both must
    reach chatgpt.com on every request. Headers the sandbox already set that we
    don't name here (e.g. Codex's Cloudflare `originator` / `User-Agent`) pass
    through untouched — only the bearer and the named headers are rewritten.

    `connect_mode` is `device`: unlike redirect OAuth, the env-resident broker
    runs OpenAI's device flow itself (no callback of ours), so the WebUI shows a
    user code rather than a redirect button.
    """

    bearer_secret: str
    header_secrets: dict[str, str]  # secret_name -> HTTP header name
    auth_format: str = AUTH_FORMAT_BEARER
    connect_mode: ClassVar[ConnectMode] = "device"


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
class VaultUrlRewrite:
    """Vault-pasted credential; injected by replacing a placeholder in the URL.

    The sandbox client uses `placeholder` in the URL where the real secret
    would go (e.g. Telegram's `/bot{token}/` path); the proxy substitutes
    the live token before forwarding.

    Env vars that expose this placeholder or provider config are declared on
    `TlsProviderSpec.env_bindings`.
    """

    placeholder: str
    connect_mode: ClassVar[ConnectMode] = "vault"


@dataclass(frozen=True)
class VaultHeaderInject:
    """Vault-pasted, header-injected, multi-secret credential (Slack).

    Hybrid of the other two methods: it's vault-pasted like `VaultUrlRewrite`,
    but the secret rides an `Authorization: Bearer` header like `OAuthHeader`
    rather than a URL placeholder.

    A provider here carries more than one secret (Slack's app + bot token).
    Selection is by **placeholder reverse-map, not request path**: the gateway
    env hands the sandbox a distinct placeholder bearer per secret, and the
    sandbox already sends the correct token per call (the app token opens the
    Socket Mode connection; the bot token posts messages). The proxy reads the
    incoming placeholder bearer and swaps in the matching real secret — no
    per-request path logic. `placeholders` maps secret_name -> placeholder.
    """

    placeholders: dict[str, str]
    auth_format: str = AUTH_FORMAT_BEARER
    connect_mode: ClassVar[ConnectMode] = "vault"

    def secret_for_placeholder(self, bearer_token: str) -> str | None:
        """Reverse-map an incoming placeholder bearer to its secret name."""
        for secret_name, placeholder in self.placeholders.items():
            if placeholder == bearer_token:
                return secret_name
        return None


@dataclass(frozen=True)
class VaultApiKeyHeader:
    """Vault-pasted, single-secret credential injected as a custom auth header (Anthropic).

    Like `VaultHeaderInject`, but the credential rides a provider-specific
    header (Anthropic's `x-api-key`) rather than `Authorization: Bearer`. The
    sandbox sends the placeholder as that header's value; the proxy confirms it
    matches `placeholder` (so the request is ours), then swaps in the real key.
    Every other client header — notably Anthropic's required `anthropic-version`
    — passes through untouched, and no `Authorization` header is added.
    """

    header_name: str
    placeholder: str
    connect_mode: ClassVar[ConnectMode] = "vault"


CredentialMethod = OAuthHeader | OAuthHeaderMultiInject | VaultUrlRewrite | VaultHeaderInject | VaultApiKeyHeader


@dataclass(frozen=True)
class TlsProviderSpec:
    """Static config for one provider whose HTTPS traffic is intercepted."""

    slug: str
    label: str
    hosts: tuple[str, ...]
    logo_url: str
    credential_method: CredentialMethod
    env_bindings: tuple[EnvBinding, ...]
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
        credential_method=OAuthHeader(auth_format=AUTH_FORMAT_BEARER),
        env_bindings=(),
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
        credential_method=OAuthHeader(auth_format=AUTH_FORMAT_BASIC_X_ACCESS_TOKEN),
        env_bindings=(
            EnvBinding(env_var="GITHUB_TOKEN", value=HUMR_PLACEHOLDER_VALUE),
        ),
        restart_gateway_after_save=False,
        restart_webui_after_save=True,
        category="connector",
    ),
    TlsProviderSpec(
        slug="telegram",
        label="Telegram",
        hosts=("api.telegram.org",),
        logo_url="/extensions/humr/telegram.svg",
        credential_method=VaultUrlRewrite(
            placeholder="000000:HUMR_PLACEHOLDER",
        ),
        env_bindings=(
            EnvBinding(env_var="TELEGRAM_BOT_TOKEN", value="000000:HUMR_PLACEHOLDER"),
            EnvBinding(env_var="TELEGRAM_ALLOWED_USERS", config_key="allowed_users", list_separator=","),
        ),
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
        credential_method=VaultHeaderInject(
            placeholders={
                "app_token": "xapp-HUMR_PLACEHOLDER",
                "bot_token": "xoxb-HUMR_PLACEHOLDER",
            },
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
        credential_method=OAuthHeaderMultiInject(
            bearer_secret="access_token",
            header_secrets={"chatgpt_account_id": "ChatGPT-Account-ID"},
        ),
        env_bindings=(),
        restart_gateway_after_save=False,
        restart_webui_after_save=False,
        category="model_provider",
    ),
    TlsProviderSpec(
        slug="nous",
        label="Nous Portal",
        hosts=("inference-api.nousresearch.com",),
        logo_url="/extensions/humr/nous.svg",
        credential_method=OAuthHeader(auth_format=AUTH_FORMAT_BEARER, connect_mode="device"),
        env_bindings=(),
        restart_gateway_after_save=False,
        restart_webui_after_save=False,
        category="model_provider",
    ),
    TlsProviderSpec(
        slug="openrouter",
        label="OpenRouter",
        hosts=("openrouter.ai",),
        logo_url="/extensions/humr/openrouter.svg",
        credential_method=VaultHeaderInject(
            placeholders={"api_key": HUMR_PLACEHOLDER_VALUE},
        ),
        env_bindings=(
            EnvBinding(env_var="OPENROUTER_API_KEY", value=HUMR_PLACEHOLDER_VALUE),
        ),
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
        credential_method=VaultHeaderInject(
            placeholders={"api_key": HUMR_PLACEHOLDER_VALUE},
        ),
        env_bindings=(
            EnvBinding(env_var="OPENAI_API_KEY", value=HUMR_PLACEHOLDER_VALUE),
        ),
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
        credential_method=VaultApiKeyHeader(
            header_name="x-api-key",
            placeholder=HUMR_PLACEHOLDER_VALUE,
        ),
        env_bindings=(
            EnvBinding(env_var="ANTHROPIC_API_KEY", value=HUMR_PLACEHOLDER_VALUE),
        ),
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
        credential_method=VaultApiKeyHeader(
            header_name="X-Browser-Use-API-Key",
            placeholder=HUMR_PLACEHOLDER_VALUE,
        ),
        env_bindings=(
            EnvBinding(env_var="BROWSER_USE_API_KEY", value=HUMR_PLACEHOLDER_VALUE),
        ),
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
        credential_method=OAuthHeader(auth_format=AUTH_FORMAT_BEARER),
        env_bindings=(),
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
        credential_method=VaultHeaderInject(
            placeholders={"api_key": "tvly-HUMR_PLACEHOLDER"},
        ),
        env_bindings=(
            EnvBinding(env_var="TAVILY_API_KEY", value="tvly-HUMR_PLACEHOLDER"),
        ),
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
HOST_TO_TLS_PROVIDER = build_host_to_provider(providers=TLS_INTERCEPT_PROVIDERS)
