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
    "source_repo_path": "openclaw_agent",
    "app_type": "web",
    "build_strategy": "dockerfile",
    "dockerfile_path": "Dockerfile",
    "container_port": 18789,
    "health_check_path": "/health",
    "health_check_command": "",
    "health_check_grace_period": 60,
    "cpu": 1024,
    "memory": 2048,
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
    "datastore_config": None,
    "efs_config": {"mount_path": "/app/workspace", "posix_uid": 1000, "posix_gid": 1000},
    "is_active": True,
}


# -- Hermes shared building blocks ------------------------------------------

_HERMES_BASE = {
    "source_repo_path": "hermes_agent",
    "app_type": "web",
    "build_strategy": "dockerfile",
    "dockerfile_path": "Dockerfile",
    "container_port": 8787,
    "health_check_path": "/health",
    "health_check_command": "",
    "health_check_grace_period": 60,
    "cpu": 1024,
    "memory": 2048,
    "datastore_config": None,
    "efs_config": {"mount_path": "/home/hermeswebui/.hermes", "posix_uid": 1024, "posix_gid": 1024},
    "is_active": True,
}

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
        "default_value": "us.anthropic.claude-opus-4-6-v1",
        "value": "us.anthropic.claude-opus-4-6-v1",
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

_HERMES_CREDENTIAL_VARS = [
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
        "name": "AWS_BEDROCK_ACCESS_KEY_ID",
        "group": "Bedrock",
        "category": "secret",
        "description": "AWS access key ID for Bedrock (required when DOH_LLM_PROVIDER=bedrock)",
        "required": False,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    },
    {
        "name": "AWS_BEDROCK_SECRET_ACCESS_KEY",
        "group": "Bedrock",
        "category": "secret",
        "description": "AWS secret access key for Bedrock (required when DOH_LLM_PROVIDER=bedrock)",
        "required": False,
        "auto_generate": False,
        "default_value": None,
        "value": "",
        "user_editable": False,
    },
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
]

# -- Hermes Personal (web only, one per user) -------------------------------

HERMES_PERSONAL_TEMPLATE = {
    **_HERMES_BASE,
    "name": "AI Assistant — Hermes (Personal)",
    "slug": "hermes-personal",
    "description": (
        "Personal AI assistant powered by Hermes Agent. Web UI with tool "
        "execution, persistent memory, and self-improving skills. Deploy one "
        "per user for isolated conversations and settings."
    ),
    "icon": "⚡",
    "category": "ai-assistant",
    "runtime_variables": _HERMES_LLM_VARS + _HERMES_CREDENTIAL_VARS + _HERMES_BEDROCK_VARS,
}

# -- Hermes Slack (shared, one per org) -------------------------------------

HERMES_SLACK_TEMPLATE = {
    **_HERMES_BASE,
    "name": "AI Assistant — Hermes (Slack)",
    "slug": "hermes-slack",
    "description": (
        "Shared AI assistant powered by Hermes Agent with Slack integration. "
        "Deploy one per organization — all workspace users can DM the bot "
        "with isolated conversations. Also includes the web UI."
    ),
    "icon": "💬",
    "category": "ai-assistant",
    "runtime_variables": _HERMES_LLM_VARS + _HERMES_CREDENTIAL_VARS + _HERMES_BEDROCK_VARS + _HERMES_SLACK_VARS,
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
