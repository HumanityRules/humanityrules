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
        "description": "LLM model identifier (e.g. us.anthropic.claude-opus-4-7, us.anthropic.claude-sonnet-4-6, gpt-5.4-mini)",
        "required": True,
        "auto_generate": False,
        "default_value": "us.anthropic.claude-sonnet-4-6",
        "value": "us.anthropic.claude-sonnet-4-6",
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

_HERMES_TAVILY_VAR = [
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

_HERMES_AWS_DEFAULT_REGION_VAR = [
    {
        "name": "AWS_DEFAULT_REGION",
        "group": "AWS",
        "category": "config",
        "description": "AWS region used by AWS SDKs and CLI (e.g. us-east-1)",
        "required": False,
        "auto_generate": False,
        "default_value": "us-east-1",
        "value": "us-east-1",
        "user_editable": False,
    },
]

_HERMES_CHECKPOINT_EFS_CONFIG = {
    "mounts": [
        {
            "name": "checkpoint",
            "subpath": "checkpoint",
            "container_path": "/hermes-checkpoint",
            "posix_uid": 0,
            "posix_gid": 0,
        },
    ],
}

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
    "requires_env_bearer": True,
}


# -- Hermes Personal (web only) ---------------------------------------------

HERMES_PERSONAL_TEMPLATE = {
    "name": "AI Assistant — Hermes (Personal)",
    "slug": "hermes-personal",
    "description": (
        "Personal AI assistant powered by Hermes Agent. Fronted by the policy "
        "proxy for SSO + ABAC."
    ),
    "icon": "⚡",
    "category": "ai-assistant",
    "cpu": 2048,
    "memory": 4096,
    "default_compute_mode": "ec2",
    "datastore_config": None,
    "efs_config": _HERMES_CHECKPOINT_EFS_CONFIG,
    "platform_capabilities": ["bedrock-runtime"],
    # Force ECS to fully stop the old task before starting its replacement
    "serialize_task_replacement": True,
    # ALB targets the policy proxy; the proxy forwards to the hermes container
    # over loopback after SSO + ABAC gates pass.
    "alb_target_container": "policy-proxy",
    "containers": [
        {
            "name": "hermes",
            "image_source": "dockerfile",
            "source_repo_path": "hermes_agent",
            "dockerfile_path": "Dockerfile",
            "container_port": 8787,
            "health_check_path": "/health",
            "health_check_command": "",
            "health_check_grace_period": 60,
            "efs_mounts": ["checkpoint"],
            # Policy proxy fronts the task; pin the WebUI to loopback so only
            # the proxy (sharing the task network namespace) can reach it.
            "environment": {
                "HERMES_WEBUI_HOST": "127.0.0.1",
            },
            "host_mounts": [
                {
                    "source_path": "/var/lib/devopshero/hermes-roots/{app_slug}",
                    "container_path": "/hermes-persistent-root",
                },
            ],
            "linux_capabilities": ["SYS_ADMIN"],
            "stop_timeout": 120,
            # Placement reservation: 2 GiB so two hermes tasks fit on one
            # m8g.large (~7747 MiB usable). Hard cap: 4 GiB — the container
            # can burst there when alone on the node. Under host memory
            # pressure the kernel reclaims *reclaimable pages* (page cache,
            # swappable anonymous pages) from whichever container is above
            # its 2-GiB memory.low floor first. Usage doesn't snap back to
            # 2 GiB; each task is just guaranteed not to be evicted below
            # 2 GiB by its neighbor. If both tasks' live RSS is unreclaimable
            # and exceeds the host, one will be OOM-killed before the kernel
            # violates memory.low.
            "memory_reservation_mib": 2048,
            "memory_limit_mib": 4096,
            "configurable_variables": (
                _HERMES_LLM_VARS + _HERMES_AWS_DEFAULT_REGION_VAR + _HERMES_TAVILY_VAR
            ),
            # Supervisor process (outside the nono sandbox) refreshes Google
            # access tokens by POSTing to DOH /api/integrations/google/token.
            "requires_env_bearer": True,
        },
        # Policy proxy is tiny (httpx + starlette); 256 MiB is plenty. Must
        # be set so the task has no container without a memory cap (ECS
        # requires task-level OR per-container memory on EC2).
        {**_HERMES_POLICY_PROXY_CONTAINER, "memory_limit_mib": 256},
    ],
    "default_tags": [{"key": "app-type", "value": "personal-assistant"}],
    "prefill_name": "hermes-{username}{index}",
    "is_active": True,
}


TEMPLATES = [
    OPENCLAW_TEMPLATE,
    HERMES_PERSONAL_TEMPLATE,
]


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
