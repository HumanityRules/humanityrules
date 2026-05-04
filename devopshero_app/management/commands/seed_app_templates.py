"""
Management command to seed AppTemplate records.

Usage:
    python manage.py seed_app_templates
"""

from django.core.management.base import BaseCommand

from devopshero_app.models import AppTemplate


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
            "configurable_variables": [
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

# The WebUI's password auth is redundant when the policy proxy is enforcing
# SSO + ABAC in front of the app. The Personal template runs behind the proxy
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

# Three sibling access points on EFS per Hermes app: "home" holds agent state
# (config, memory, skills, venv) and is mounted at ~/.hermes in the hermes
# container; "workspace" holds user/agent work output and is mounted at
# /workspace in both the hermes container and the docker-dind container — same
# data visible on both sides, so files the agent creates through tool runs
# appear under ~/.workspace and vice versa; "docker-persistence" holds the
# zstd-compressed tool-container snapshots the doh-dind snapshotter writes,
# which restore into doh-toolbox:latest on every DinD boot so pip/apt/npm
# state survives ECS task restarts. Flat sibling layout (not nested inside
# home) keeps each tier's data independent on disk.
#
# docker-persistence uses uid/gid 0 because docker-dind runs dockerd as root
# and the snapshotter inherits that uid — the other two mounts stay on 1024
# (hermeswebui).
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
        {
            "name": "docker-persistence",
            "subpath": "docker-persistence",
            "container_path": "/var/lib/doh-dind/persistence",
            "posix_uid": 0,
            "posix_gid": 0,
        },
    ],
}

# Privileged; mounts workspace (shared with hermes) and docker-persistence
# (snapshotter-owned). DOH-owned image layered on docker:26.1.0-dind — see
# template_repos/doh_dind/ for the Dockerfile, entrypoint, and snapshotter.
_DOCKER_DIND_CONTAINER = {
    "name": "docker-dind",
    "image_source": "prebuilt",
    "ecr_repo": "doh-dind",
    # Image tag DOH expects in doh/{env_slug}/doh-dind. Bump when we ship a new
    # snapshotter or entrypoint in template_repos/doh_dind/ and push via
    # `doh_build_prebuilt_image --source-dir template_repos/doh_dind --ecr-repo doh-dind --tag X.Y.Z`.
    "version": "0.2.5",
    # No "command" override: the doh_dind entrypoint starts dockerd itself
    # with the right loopback bind and then exec's into snapshotter.py.
    "container_port": 0,
    "privileged": True,
    "essential": True,
    # DinD sees workspace (so containers it spawns can bind /workspace) and
    # docker-persistence (where the snapshotter reads/writes latest.tar.zst),
    # but never the agent home — tool containers it spawns can't touch
    # Hermes config, memory, or skills even if the tool container namespace
    # is compromised.
    "efs_mounts": ["workspace", "docker-persistence"],
    "health_check_path": None,
    # The marker file is touched by /entrypoint.sh after restore completes, so
    # Hermes (depends_on: HEALTHY) can't race the first `docker run` against a
    # missing doh-toolbox:latest tag.
    "health_check_command": (
        "docker -H tcp://127.0.0.1:2375 info >/dev/null 2>&1 "
        "&& test -f /var/run/doh-restore-ready"
    ),
    "health_check_grace_period": 180,
    # stop_timeout: give the snapshotter 120s to flush its SIGTERM-triggered
    # snapshot to EFS before ECS SIGKILLs us. Default ECS stop timeout is 30s,
    # which isn't enough to docker-export a multi-GB rootfs.
    "stop_timeout": 120,
    # Empty DOCKER_TLS_CERTDIR disables TLS on the dockerd listener, which is
    # required because we bind on tcp://127.0.0.1:2375 for in-task loopback.
    # TOOL_IMAGE_BASE is the fallback base image /entrypoint.sh pulls on
    # fresh boot (when no snapshot exists on EFS yet).
    "environment": {
        "DOCKER_TLS_CERTDIR": "",
        "TOOL_IMAGE_BASE": "nikolaik/python-nodejs:python3.11-nodejs20",
        # Our entrypoint.sh is PID 1, not tini — so tini's default zombie
        # reaping (which only activates when it's PID 1) is off. Setting
        # TINI_SUBREAPER=1 makes tini call PR_SET_CHILD_SUBREAPER on itself
        # so dockerd's short-lived children get reaped instead of lingering.
        "TINI_SUBREAPER": "1",
    },
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
    # Platform-constant env vars the operator never touches. Kept out of
    # configurable_variables so the deploy form doesn't treat them as knobs.
    "environment": {
        # DOCKER_HOST targets the in-task DinD container over loopback.
        "DOCKER_HOST": "tcp://127.0.0.1:2375",
        # Image ref Hermes's terminal tool launches via `docker run`. This
        # is ALWAYS a local tag — the doh-dind sidecar restores it from
        # snapshot (or pulls TOOL_IMAGE_BASE as a fallback) before its
        # healthcheck goes green, so Hermes never pulls from a registry.
        "TOOL_IMAGE": "doh-toolbox:latest",
        # Upstream's terminal idle reaper checks this value before calling
        # cleanup. Keep it high so idle recycling is not the normal path; other
        # lifecycle cleanup can still stop persistent containers.
        "TERMINAL_LIFETIME_SECONDS": "86400",
        # TL;DR: turns off per-container disk size limits inside DinD
        # because our filesystem can't enforce them, and trying causes
        # tool containers to fail to start. Task-level limits still apply.
        "TERMINAL_CONTAINER_DISK": "0",
    },
    # Mirror doh-dind's stop_timeout so Hermes's own atexit/SIGTERM handling
    # has headroom. Hermes doesn't snapshot itself, but it does try to
    # docker-stop its tool container on shutdown, which hits DinD.
    "stop_timeout": 120,
}

# Upstream credentials consumed by the learneo-mcp aggregator. All empty
# values — resolve from env shared-secrets via _resolve_secret_value at
# deploy time (ops populates devopshero/{env_slug}/shared-secrets once).
_LEARNEO_MCP_UPSTREAM_SECRET_NAMES = [
    "LEARNEO_MCP_GITLAB_TOKEN",
    "LEARNEO_MCP_GITLAB_URL",
    "LEARNEO_MCP_ATLASSIAN_TOKEN",
    "LEARNEO_MCP_ATLASSIAN_EMAIL",
    "LEARNEO_MCP_ATLASSIAN_URL",
    "LEARNEO_MCP_GITHUB_TOKEN",
    "LEARNEO_MCP_GROUNDCOVER_TOKEN",
    "LEARNEO_MCP_GROUNDCOVER_TIMEZONE",
    "LEARNEO_MCP_GROUNDCOVER_TENANT_UUID",
    "LEARNEO_MCP_DATADOG_API_KEY",
    "LEARNEO_MCP_DATADOG_APP_KEY",
    "LEARNEO_MCP_DATADOG_SITE",
    "LEARNEO_MCP_CONTROLMONKEY_TOKEN",
    "LEARNEO_MCP_CONTROLMONKEY_URL",
    "LEARNEO_MCP_GCP_TOKEN",
    "LEARNEO_MCP_CLOUDFLARE_TOKEN",
]

_LEARNEO_MCP_UPSTREAM_SECRETS = [
    {
        "name": name,
        "group": "Learneo MCP upstreams",
        "category": "secret",
        "description": f"{name} for the learneo-mcp aggregator (inherits from env shared-secrets when empty)",
        "required": False,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    }
    for name in _LEARNEO_MCP_UPSTREAM_SECRET_NAMES
]

# Policy proxy: platform-owned SSO+ABAC gate that fronts the Hermes container.
# image_source="policy_proxy" is resolved at deploy time to the per-env ECR
# repo (doh/{env_slug}/policy-proxy:POLICY_PROXY_IMAGE_VERSION), and its
# presence drives env-level provisioning (auth Lambda, per-env secrets).
# Listens on hermes.container_port + 1 to keep the upstream port free.
_HERMES_POLICY_PROXY_CONTAINER = {
    "name": "policy-proxy",
    "image_source": "policy_proxy",
    "upstream_container": "hermes",
    "container_port": 8788,
    "health_check_path": "/__policy_proxy/healthz",
    "health_check_command": "",
    "health_check_grace_period": 0,
    "depends_on": [
        {
            "name": "hermes",
            "condition": "START",
        },
    ],
    "environment": {},
    "configurable_variables": [],
}


_LEARNEO_MCP_CONTAINER = {
    "name": "learneo-mcp",
    "image_source": "prebuilt",
    "ecr_repo": "learneo-mcp",
    # Image tag DOH expects in doh/{env_slug}/learneo-mcp. Bump in lock-step
    # with whatever the operator has built and pushed into the customer's ECR.
    "version": "0.1.0",
    # Override the image default (stdio mode, which exits immediately on EOF
    # under ECS awsvpc). --host 127.0.0.1 keeps the endpoint loopback-only.
    "command": ["--http", "--port", "7777", "--host", "127.0.0.1"],
    "container_port": 7777,
    # Non-essential: an MCP crash leaves Hermes running (degraded — no tool
    # access, but the LLM still answers). No health check either, for the
    # same reason + there's no /health endpoint exposed.
    "essential": False,
    "configurable_variables": _LEARNEO_MCP_UPSTREAM_SECRETS,
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
    # ALB targets the policy proxy; the proxy forwards to the hermes container
    # over loopback after SSO + ABAC gates pass.
    "alb_target_container": "policy-proxy",
    "containers": [
        {**_DOCKER_DIND_CONTAINER},
        {
            **_HERMES_CONTAINER_BASE,
            # Policy proxy fronts the task; pin the WebUI to loopback so only
            # the proxy (sharing the task network namespace) can reach it.
            "environment": {
                **_HERMES_CONTAINER_BASE["environment"],
                "HERMES_WEBUI_HOST": "127.0.0.1",
            },
            "configurable_variables": (
                _HERMES_LLM_VARS + _HERMES_CREDENTIAL_VARS + _HERMES_BEDROCK_VARS
            ),
        },
        {**_HERMES_POLICY_PROXY_CONTAINER},
    ],
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
    # cover Hermes + the learneo-mcp container.
    "cpu": 2048,
    "memory": 4096,
    "default_compute_mode": "ec2",
    "datastore_config": None,
    "efs_config": _HERMES_EFS_CONFIG,
    "platform_capabilities": ["bedrock-runtime"],
    "alb_target_container": "hermes",
    "containers": [
        {**_DOCKER_DIND_CONTAINER},
        {
            **_HERMES_CONTAINER_BASE,
            "configurable_variables": (
                _HERMES_LLM_VARS + _HERMES_WEBUI_PASSWORD_VAR + _HERMES_CREDENTIAL_VARS
                + _HERMES_BEDROCK_VARS + _HERMES_SLACK_VARS
            ),
        },
        _LEARNEO_MCP_CONTAINER,
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
