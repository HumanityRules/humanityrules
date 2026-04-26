"""
Management command to seed AppTemplate records.

Usage:
    python manage.py seed_app_templates
"""

from django.core.management.base import BaseCommand

from devopshero_app.models import AppTemplate


# Image tag DOH expects in doh/{env_slug}/sidecar-mcp. Bump in lock-step with
# whatever the operator has built and pushed into the customer's ECR.
SIDECAR_MCP_IMAGE_VERSION = "0.1.0"


OPENCLAW_TEMPLATE = {
    "name": "AI Assistant (OpenClaw)",
    "slug": "ai-assistant-openclaw",
    "description": (
        "Deploy a governed AI personal assistant powered by OpenClaw. "
        "Includes tool execution, persistent memory, and Slack integration. "
        "Runs inside your VPC with your choice of model provider."
    ),
    "icon": "🤖",
    "category": "ai-assistant",
    "cpu": 1024,
    "memory": 2048,
    "default_compute_mode": "fargate",
    "alb_target_container": "app",
    "containers": [
        {
            "name": "app",
            "image_source": "dockerfile",
            "source_repo_path": "openclaw_agent",
            "dockerfile_path": "Dockerfile",
            "container_port": 18789,
            "health_check_path": "/health",
            "health_check_command": "",
            "health_check_grace_period": 60,
            "efs_mounts": ["workspace"],
            "runtime_variables": [
                {
                    "name": "OPENCLAW_LOG_LEVEL",
                    "group": "Configuration",
                    "category": "config",
                    "description": "Log level: debug, info, warn, error",
                    "required": True,
                    "auto_generate": False,
                    "default_value": "info",
                    "value": "info",
                    "user_editable": False,
                },
                {
                    "name": "OPENCLAW_DEFAULT_MODEL",
                    "group": "Configuration",
                    "category": "config",
                    "description": "Default LLM model identifier",
                    "required": True,
                    "auto_generate": False,
                    "default_value": "openai/gpt-5.4-nano",
                    "value": "openai/gpt-5.4-nano",
                    "user_editable": False,
                },
                {
                    "name": "OPENCLAW_GATEWAY_TOKEN",
                    "group": "Authentication",
                    "category": "secret",
                    "description": "Gateway authentication token (auto-generated)",
                    "required": True,
                    "auto_generate": True,
                    "default_value": None,
                    "value": None,
                    "user_editable": False,
                },
                {
                    "name": "ANTHROPIC_API_KEY",
                    "group": "LLM Providers",
                    "category": "secret",
                    "description": "Anthropic API key for Claude models",
                    "required": False,
                    "auto_generate": False,
                    "default_value": None,
                    "value": "",
                    "user_editable": False,
                },
                {
                    "name": "OPENAI_API_KEY",
                    "group": "LLM Providers",
                    "category": "secret",
                    "description": "OpenAI API key (alternative provider)",
                    "required": False,
                    "auto_generate": False,
                    "default_value": None,
                    "value": "",
                    "user_editable": False,
                },
                {
                    "name": "TAVILY_API_KEY",
                    "group": "Integrations",
                    "category": "secret",
                    "description": "Tavily API key for web search capability",
                    "required": False,
                    "auto_generate": False,
                    "default_value": None,
                    "value": "",
                    "user_editable": False,
                },
                {
                    "name": "SLACK_APP_TOKEN",
                    "group": "Slack",
                    "category": "secret",
                    "description": "Slack app-level token (leave empty to disable Slack)",
                    "required": False,
                    "auto_generate": False,
                    "default_value": None,
                    "value": "",
                    "user_editable": False,
                },
                {
                    "name": "SLACK_BOT_TOKEN",
                    "group": "Slack",
                    "category": "secret",
                    "description": "Slack bot token (leave empty to disable Slack)",
                    "required": False,
                    "auto_generate": False,
                    "default_value": None,
                    "value": "",
                    "user_editable": False,
                },
            ],
        },
    ],
    "datastore_config": None,
    "efs_config": {
        "mounts": [
            {
                "name": "workspace",
                "subpath": "workspace",
                "container_path": "/app/workspace",
                "posix_uid": 1000,
                "posix_gid": 1000,
            },
        ],
    },
    "is_active": True,
}


# -- Hermes shared building blocks ------------------------------------------

_HERMES_LLM_VARS = [
    {
        "name": "DOH_LLM_PROVIDER",
        "group": "Main LLM",
        "category": "config",
        "description": "Hermes provider name: bedrock, custom, anthropic, or openrouter",
        "required": True,
        "auto_generate": False,
        "default_value": "bedrock",
        "value": "bedrock",
        "user_editable": True,
    },
    {
        "name": "DOH_LLM_MODEL",
        "group": "Main LLM",
        "category": "config",
        "description": "LLM model identifier (e.g. us.anthropic.claude-opus-4-7, us.anthropic.claude-opus-4-6-v1, gpt-5.4-mini)",
        "required": True,
        "auto_generate": False,
        "default_value": "us.anthropic.claude-opus-4-7",
        "value": "us.anthropic.claude-opus-4-7",
        "user_editable": True,
    },
    {
        "name": "DOH_LLM_BASE_URL",
        "group": "Main LLM",
        "category": "config",
        "description": "Provider API base URL (required for custom provider, auto-derived for bedrock, empty otherwise)",
        "required": False,
        "auto_generate": False,
        "default_value": "",
        "value": "",
        "user_editable": True,
    },
    {
        "name": "DOH_AUX_PROVIDER",
        "group": "Auxiliary LLM",
        "category": "config",
        "description": "Auxiliary LLM provider for vision, compression, session_search, skills_hub, approval, mcp, flush_memories, web_extract (defaults to main provider when unset)",
        "required": False,
        "auto_generate": False,
        "default_value": "bedrock",
        "value": "bedrock",
        "user_editable": True,
    },
    {
        "name": "DOH_AUX_MODEL",
        "group": "Auxiliary LLM",
        "category": "config",
        "description": "Auxiliary LLM model — cheaper/faster than main (e.g. us.anthropic.claude-sonnet-4-6, gpt-5.4-mini)",
        "required": False,
        "auto_generate": False,
        "default_value": "us.anthropic.claude-sonnet-4-6",
        "value": "us.anthropic.claude-sonnet-4-6",
        "user_editable": True,
    },
    {
        "name": "DOH_AUX_BASE_URL",
        "group": "Auxiliary LLM",
        "category": "config",
        "description": "Auxiliary provider API base URL (required for custom, auto-derived for bedrock, empty otherwise)",
        "required": False,
        "auto_generate": False,
        "default_value": "",
        "value": "",
        "user_editable": True,
    },
]

# The WebUI's password auth is redundant when the sidecar proxy is enforcing
# SSO + ABAC in front of the app. The Personal template runs behind the sidecar
# and drops this var (no env var set -> WebUI auth disabled, per api/auth.py).
# The Slack template is ALB-exposed with no SSO gate, so it still needs it.
_HERMES_WEBUI_PASSWORD_VAR = [
    {
        "name": "HERMES_WEBUI_PASSWORD",
        "group": "Authentication",
        "category": "secret",
        "description": "WebUI access password",
        "required": True,
        "auto_generate": False,
        "default_value": None,
        "value": "mysquirrel",
        "user_editable": False,
    },
]

_HERMES_CREDENTIAL_VARS = [
    {
        "name": "ANTHROPIC_API_KEY",
        "group": "API Keys",
        "category": "secret",
        "description": "Anthropic API key for Claude models",
        "required": False,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    },
    {
        "name": "OPENAI_API_KEY",
        "group": "API Keys",
        "category": "secret",
        "description": "OpenAI API key (alternative provider)",
        "required": False,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    },
    {
        "name": "OPENROUTER_API_KEY",
        "group": "API Keys",
        "category": "secret",
        "description": "OpenRouter API key for multi-model access (200+ models)",
        "required": False,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    },
    {
        "name": "TAVILY_API_KEY",
        "group": "API Keys",
        "category": "secret",
        "description": "Tavily API key for web search capability",
        "required": False,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    },
]

_HERMES_BEDROCK_VARS = [
    {
        "name": "AWS_BEDROCK_REGION",
        "group": "Bedrock",
        "category": "config",
        "description": "AWS region for Bedrock (e.g. us-east-1)",
        "required": False,
        "auto_generate": False,
        "default_value": "us-east-1",
        "value": "us-east-1",
        "user_editable": False,
    },
]

_HERMES_SLACK_VARS = [
    {
        "name": "SLACK_APP_TOKEN",
        "group": "Slack",
        "category": "secret",
        "description": "Slack app-level token",
        "required": True,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    },
    {
        "name": "SLACK_BOT_TOKEN",
        "group": "Slack",
        "category": "secret",
        "description": "Slack bot token",
        "required": True,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    },
    {
        "name": "SLACK_ALLOW_ALL_USERS",
        "group": "Slack",
        "category": "config",
        "description": "Allow all Slack users to interact with the bot (true/false)",
        "required": False,
        "auto_generate": False,
        "default_value": "true",
        "value": "true",
        "user_editable": False,
    },
    {
        "name": "SLACK_ALLOWED_USERS",
        "group": "Slack",
        "category": "config",
        "description": "Comma-separated Slack Member IDs (overrides allow-all when set)",
        "required": False,
        "auto_generate": False,
        "default_value": "",
        "value": "",
        "user_editable": False,
    },
    {
        "name": "SLACK_HOME_CHANNEL",
        "group": "Slack",
        "category": "config",
        "description": "Slack channel ID for scheduled messages, cron results, and proactive notifications (e.g. C01234567890). Bot must be invited to this channel.",
        "required": False,
        "auto_generate": False,
        "default_value": "test-channel",
        "value": "test-channel",
        "user_editable": True,
    },
]

# Two sibling access points on EFS per Hermes app: "home" holds agent state
# (config, memory, skills, venv) and is mounted at ~/.hermes in the hermes
# container; "workspace" holds user/agent work output and is mounted at
# /workspace in both the hermes container and the docker-dind sidecar — same
# data visible on both sides, so files the agent creates through tool runs
# appear under ~/.workspace and vice versa. Flat sibling layout (not nested
# inside home) keeps agent home and workspace data independent on disk.
_HERMES_EFS_CONFIG = {
    "mounts": [
        {
            "name": "home",
            "subpath": "hermes",
            "container_path": "/home/hermeswebui/.hermes",
            "posix_uid": 1024,
            "posix_gid": 1024,
        },
        {
            "name": "workspace",
            "subpath": "workspace",
            "container_path": "/workspace",
            "posix_uid": 1024,
            "posix_gid": 1024,
        },
    ],
}

# Hermes tool containers (terminal backend: docker) talk to this task's DinD sidecar.
_HERMES_DOCKER_HOST_VAR = {
    "name": "DOCKER_HOST",
    "group": "Tools",
    "category": "config",
    "description": "Task-local Docker daemon (DinD sidecar) for Hermes tools",
    "required": True,
    "auto_generate": False,
    "default_value": "tcp://127.0.0.1:2375",
    "value": "tcp://127.0.0.1:2375",
    "user_editable": False,
}

# Privileged; mounts only the workspace EFS access point. Same repo root as other containers.
_DOCKER_DIND_CONTAINER = {
    "name": "docker-dind",
    "image_source": "registry",
    "registry_image": "docker:26.1.0-dind",
    # docker:dind's entrypoint (dockerd-entrypoint.sh) prepends a default
    # --host=tcp://0.0.0.0:2375 whenever the first CMD arg starts with '-',
    # which would collide with our loopback bind on the same port. Pass
    # 'dockerd' as the first arg to suppress that default.
    "command": [
        "dockerd",
        "--host=unix:///var/run/docker.sock",
        "--host=tcp://127.0.0.1:2375",
    ],
    "container_port": 0,
    "privileged": True,
    "essential": True,
    # DinD sees the workspace mount only — never the agent home. That keeps
    # tool containers it spawns unable to read/modify Hermes config, memory,
    # or skills even if an attacker escapes the tool container's namespace.
    "efs_mounts": ["workspace"],
    "health_check_path": None,
    "health_check_command": "docker info >/dev/null 2>&1",
    "health_check_grace_period": 120,
    "runtime_variables": [
        {
            "name": "DOCKER_TLS_CERTDIR",
            "group": "Tools",
            "category": "config",
            "description": "Empty disables Docker TLS; required for tcp 127.0.0.1:2375",
            "required": True,
            "auto_generate": False,
            "default_value": "",
            "value": "",
            "allow_empty_value": True,
            "user_editable": False,
        },
    ],
}

_HERMES_CONTAINER_BASE = {
    "name": "hermes",
    "image_source": "dockerfile",
    "source_repo_path": "hermes_agent",
    "dockerfile_path": "Dockerfile",
    "container_port": 8787,
    "health_check_path": "/health",
    "health_check_command": "",
    "health_check_grace_period": 60,
    "efs_mounts": ["home", "workspace"],
    "depends_on": [
        {
            "name": "docker-dind",
            "condition": "HEALTHY",
        },
    ],
}

# Upstream credentials consumed by the sidecar-mcp aggregator. All empty
# values — resolve from env shared-secrets via _resolve_secret_value at
# deploy time (ops populates devopshero/{env_slug}/shared-secrets once).
_SIDECAR_MCP_UPSTREAM_SECRET_NAMES = [
    "SIDECAR_MCP_GITLAB_TOKEN",
    "SIDECAR_MCP_GITLAB_URL",
    "SIDECAR_MCP_ATLASSIAN_TOKEN",
    "SIDECAR_MCP_ATLASSIAN_EMAIL",
    "SIDECAR_MCP_ATLASSIAN_URL",
    "SIDECAR_MCP_GITHUB_TOKEN",
    "SIDECAR_MCP_GROUNDCOVER_TOKEN",
    "SIDECAR_MCP_GROUNDCOVER_TIMEZONE",
    "SIDECAR_MCP_GROUNDCOVER_TENANT_UUID",
    "SIDECAR_MCP_DATADOG_API_KEY",
    "SIDECAR_MCP_DATADOG_APP_KEY",
    "SIDECAR_MCP_DATADOG_SITE",
    "SIDECAR_MCP_CONTROLMONKEY_TOKEN",
    "SIDECAR_MCP_CONTROLMONKEY_URL",
    "SIDECAR_MCP_GCP_TOKEN",
    "SIDECAR_MCP_CLOUDFLARE_TOKEN",
]

_SIDECAR_MCP_UPSTREAM_SECRETS = [
    {
        "name": name,
        "group": "Sidecar MCP upstreams",
        "category": "secret",
        "description": f"{name} for the sidecar-mcp aggregator (inherits from env shared-secrets when empty)",
        "required": False,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    }
    for name in _SIDECAR_MCP_UPSTREAM_SECRET_NAMES
]

_SIDECAR_MCP_CONTAINER = {
    "name": "sidecar-mcp",
    "image_source": "prebuilt",
    "ecr_repo": "sidecar-mcp",
    "version": SIDECAR_MCP_IMAGE_VERSION,
    # Override the image default (stdio mode, which exits immediately on EOF
    # under ECS awsvpc). --host 127.0.0.1 keeps the endpoint loopback-only.
    "command": ["--http", "--port", "7777", "--host", "127.0.0.1"],
    "container_port": 7777,
    # Non-essential: an MCP crash leaves Hermes running (degraded — no tool
    # access, but the LLM still answers). No health check either, for the
    # same reason + there's no /health endpoint exposed.
    "essential": False,
    "runtime_variables": _SIDECAR_MCP_UPSTREAM_SECRETS,
}


# -- Hermes Personal (web only, one per user) -------------------------------

HERMES_PERSONAL_TEMPLATE = {
    "name": "AI Assistant — Hermes (Personal)",
    "slug": "hermes-personal",
    "description": (
        "Personal AI assistant powered by Hermes Agent. Web UI with tool "
        "execution, persistent memory, and self-improving skills. Deploy one "
        "per user for isolated conversations and settings."
    ),
    "icon": "⚡",
    "category": "ai-assistant",
    "cpu": 2048,
    "memory": 4096,
    "default_compute_mode": "ec2",
    "datastore_config": None,
    "efs_config": _HERMES_EFS_CONFIG,
    "alb_target_container": "hermes",
    "containers": [
        {**_DOCKER_DIND_CONTAINER},
        {
            **_HERMES_CONTAINER_BASE,
            # Bind the WebUI to loopback so only the sidecar (sharing the
            # task network namespace) can reach it. Overrides the upstream
            # image default of HERMES_WEBUI_HOST=0.0.0.0, which would
            # otherwise expose the WebUI on the task ENI to the whole VPC.
            "runtime_variables": (
                _HERMES_LLM_VARS + _HERMES_CREDENTIAL_VARS + _HERMES_BEDROCK_VARS + [
                    {
                        "name": "HERMES_WEBUI_HOST",
                        "group": "Authentication",
                        "category": "config",
                        "description": "Bind address for the WebUI (loopback-only; sidecar reaches it via 127.0.0.1)",
                        "required": True,
                        "auto_generate": False,
                        "default_value": "127.0.0.1",
                        "value": "127.0.0.1",
                        "user_editable": False,
                    },
                    _HERMES_DOCKER_HOST_VAR,
                ]
            ),
        },
    ],
    # Runs behind the sidecar proxy: SSO + ABAC gate the WebUI.
    "sidecar_enabled": True,
    "platform_capabilities": ["bedrock-runtime"],
    # The "app-type" tag is what the global PA ABAC policy matches on.
    # The "owner" tag is stamped per-deployment from the deploy form.
    "default_tags": [{"key": "app-type", "value": "personal-assistant"}],
    "prefill_name": "hermes-{username}{index}",
    "is_active": True,
}

# -- Hermes Slack (shared, one per org) -------------------------------------

HERMES_SLACK_TEMPLATE = {
    "name": "AI Assistant — Hermes (Slack)",
    "slug": "hermes-slack",
    "description": (
        "Shared AI assistant powered by Hermes Agent with Slack integration. "
        "Deploy one per organization — all workspace users can DM the bot "
        "with isolated conversations. Also includes the web UI."
    ),
    "icon": "💬",
    "category": "ai-assistant",
    # Two containers share the task's CPU/memory — bumped from 1024/2048 to
    # cover Hermes + the sidecar-mcp sidecar.
    "cpu": 2048,
    "memory": 4096,
    "default_compute_mode": "ec2",
    "datastore_config": None,
    "efs_config": _HERMES_EFS_CONFIG,
    "sidecar_enabled": False,
    "platform_capabilities": ["bedrock-runtime"],
    "alb_target_container": "hermes",
    "containers": [
        {**_DOCKER_DIND_CONTAINER},
        {
            **_HERMES_CONTAINER_BASE,
            "runtime_variables": (
                _HERMES_LLM_VARS + _HERMES_WEBUI_PASSWORD_VAR + _HERMES_CREDENTIAL_VARS
                + _HERMES_BEDROCK_VARS + _HERMES_SLACK_VARS + [_HERMES_DOCKER_HOST_VAR]
            ),
        },
        _SIDECAR_MCP_CONTAINER,
    ],
    "is_active": True,
}


TEMPLATES = [OPENCLAW_TEMPLATE, HERMES_PERSONAL_TEMPLATE, HERMES_SLACK_TEMPLATE]


class Command(BaseCommand):
    help = "Create or update AppTemplate records"

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for tpl in TEMPLATES:
            slug = tpl["slug"]
            _, created = AppTemplate.objects.update_or_create(
                slug=slug,
                defaults=tpl,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action}: {tpl['name']} ({slug})")
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done: {created_count} created, {updated_count} updated")
        )
