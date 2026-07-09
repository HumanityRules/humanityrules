"""
Management command to seed AppTemplate records.

Usage:
    python manage.py seed_app_templates
"""

from django.core.management.base import BaseCommand

from humanityrules_app.models import AppTemplate


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
        "name": "HUMR_LLM_PROVIDER",
        "group": "Main LLM",
        "category": "config",
        "description": "Hermes provider name: bedrock, custom, anthropic, openrouter, or nous",
        "required": True,
        "auto_generate": False,
        "default_value": "bedrock",
        "value": "bedrock",
        "user_editable": True,
    },
    {
        "name": "HUMR_LLM_MODEL",
        "group": "Main LLM",
        "category": "config",
        "description": "LLM model identifier (e.g. global.anthropic.claude-sonnet-4-6, global.anthropic.claude-opus-4-8, gpt-5.4-mini)",
        "required": True,
        "auto_generate": False,
        "default_value": "global.anthropic.claude-sonnet-4-6",
        "value": "global.anthropic.claude-sonnet-4-6",
        "user_editable": True,
    },
    {
        "name": "HUMR_LLM_BASE_URL",
        "group": "Main LLM",
        "category": "config",
        "description": "Provider API base URL (required for custom provider, auto-derived for bedrock and nous, empty otherwise)",
        "required": False,
        "auto_generate": False,
        "default_value": "",
        "value": "",
        "user_editable": True,
    },
    {
        "name": "HUMR_AUX_PROVIDER",
        "group": "Auxiliary LLM",
        "category": "config",
        "description": "Provider for lightweight Hermes auxiliary tasks; higher-judgment slots use the main provider",
        "required": False,
        "auto_generate": False,
        "default_value": "bedrock",
        "value": "bedrock",
        "user_editable": True,
    },
    {
        "name": "HUMR_AUX_MODEL",
        "group": "Auxiliary LLM",
        "category": "config",
        "description": "Model for lightweight Hermes auxiliary tasks (e.g. global.anthropic.claude-sonnet-4-6, gpt-5.4-mini)",
        "required": False,
        "auto_generate": False,
        "default_value": "global.anthropic.claude-sonnet-4-6",
        "value": "global.anthropic.claude-sonnet-4-6",
        "user_editable": True,
    },
    {
        "name": "HUMR_AUX_BASE_URL",
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
# repo (humr/{env_slug}/policy-proxy:POLICY_PROXY_IMAGE_VERSION), and its
# presence drives env-level provisioning (auth Lambda, per-env secrets).
# Listens on hermes.container_port + 1 to keep the upstream port free.
_HERMES_POLICY_PROXY_CONTAINER = {
    "name": "policy-proxy",
    "image_source": "policy_proxy",
    "upstream_container": "hermes",
    "container_port": 8788,
    "health_check_path": "/__policy_proxy/healthz",
    "health_check_command": "",
    # healthz reports healthy only once the upstream accepts connections, so
    # the ECS grace must cover worst-case Hermes boot (webui.sh alone allows
    # the WebUI up to ~2 min) or ECS kills still-booting tasks.
    "health_check_grace_period": 300,
    "depends_on": [
        {
            "name": "hermes",
            "condition": "START",
        },
    ],
    "environment": {},
    "configurable_variables": [],
}


# -- Hermes Personal (web only) ---------------------------------------------

HERMES_PERSONAL_TEMPLATE = {
    "name": "AI Assistant - Hermes",
    "slug": "hermes-personal",
    "description": (
        "Personal AI assistant powered by the Hermes Agent."
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
    # User webapps registered through the in-container `webapps` CLI are
    # served at <slug>.<agent-host>, so the agent needs a per-agent wildcard
    # cert + DNS + ALB host condition. See docs/webapps_design.md.
    "enable_subhosting": True,
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
            "environment": {
                "HUMR_MERGE_INTEGRATION_ENABLED": "true",
            },
            "host_mounts": [
                {
                    "source_path": "/var/lib/humr/hermes-roots/{app_slug}",
                    "container_path": "/hermes-persistent-root",
                },
            ],
            "linux_capabilities": ["SYS_ADMIN"],
            "stop_timeout": 120,
            # Placement reservation: 896 CPU units. The proxy reserves the
            # other 128, so a task totals 1024 — half a t4g.large (2048 CPU
            # units). Two tasks per node is the hard ceiling anyway: awsvpc
            # gives each task an ENI and a .large has 3 (one for the host),
            # so reservations are sized to claim half the node. Linux CPU
            # shares — not a hard cap — let a task burst to the full node
            # when its neighbor is idle.
            "cpu_reservation": 896,
            # Placement reservation: 3.25 GiB so a task totals 3584 MiB with
            # the proxy's 256 — half a t4g.large's ~7600 MiB usable. Far above
            # observed usage (hermes RSS runs 500-1000 MiB, worst peak ~1.2
            # GiB); since the ENI limit fixes packing at two tasks, a generous
            # memory.low floor costs nothing and maximizes each task's
            # protection from its neighbor. Hard cap: 4 GiB. Under host
            # memory pressure the kernel reclaims *reclaimable pages* (page
            # cache, swappable anonymous pages) from whichever container is
            # above its floor first. If both tasks' live RSS is unreclaimable
            # and exceeds the host, an above-floor container is OOM-killed
            # before the kernel violates memory.low.
            "memory_reservation_mib": 3328,
            "memory_limit_mib": 4096,
            "configurable_variables": (
                _HERMES_LLM_VARS + _HERMES_AWS_DEFAULT_REGION_VAR
            ),
            # Supervisor process (outside the nono sandbox) refreshes provider
            # access tokens by POSTing to HUMR /api/integrations/tokens.
            "requires_env_bearer": True,
        },
        # Policy proxy is tiny (httpx + starlette); 256 MiB is plenty. Must
        # be set so the task has no container without a memory cap (ECS
        # requires task-level OR per-container memory on EC2).
        {**_HERMES_POLICY_PROXY_CONTAINER, "cpu_reservation": 128, "memory_limit_mib": 256},
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
