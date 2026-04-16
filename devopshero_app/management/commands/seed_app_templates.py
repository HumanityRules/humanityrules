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
            "category": "config",
            "description": "Log level: debug, info, warn, error",
            "required": True,
            "auto_generate": False,
            "default_value": "info",
            "value": "info",
        },
        {
            "name": "OPENCLAW_DEFAULT_MODEL",
            "category": "config",
            "description": "Default LLM model identifier",
            "required": True,
            "auto_generate": False,
            "default_value": "openai/gpt-5.4-nano",
            "value": "openai/gpt-5.4-nano",
        },
        {
            "name": "OPENCLAW_GATEWAY_TOKEN",
            "category": "secret",
            "description": "Gateway authentication token (auto-generated)",
            "required": True,
            "auto_generate": True,
            "default_value": None,
            "value": None,
        },
        {
            "name": "ANTHROPIC_API_KEY",
            "category": "secret",
            "description": "Anthropic API key for Claude models",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "OPENAI_API_KEY",
            "category": "secret",
            "description": "OpenAI API key (alternative provider)",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "TAVILY_API_KEY",
            "category": "secret",
            "description": "Tavily API key for web search capability",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "SLACK_APP_TOKEN",
            "category": "secret",
            "description": "Slack app-level token (leave empty to disable Slack)",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "SLACK_BOT_TOKEN",
            "category": "secret",
            "description": "Slack bot token (leave empty to disable Slack)",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
    ],
    "datastore_config": None,
    "efs_config": {"mount_path": "/app/workspace", "posix_uid": 1000, "posix_gid": 1000},
    "is_active": True,
}


HERMES_TEMPLATE = {
    "name": "AI Assistant (Hermes)",
    "slug": "ai-assistant-hermes",
    "description": (
        "Deploy a governed AI personal assistant powered by Hermes Agent. "
        "Includes tool execution, persistent memory, self-improving skills, "
        "and multi-channel access (Slack, web). Runs inside your VPC with "
        "your choice of model provider."
    ),
    "icon": "⚡",
    "category": "ai-assistant",
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
    "runtime_variables": [
        {
            "name": "DOH_LLM_PROVIDER",
            "category": "config",
            "description": "Hermes provider name: custom, anthropic, or openrouter",
            "required": True,
            "auto_generate": False,
            "default_value": "custom",
            "value": "custom",
        },
        {
            "name": "DOH_LLM_MODEL",
            "category": "config",
            "description": "LLM model identifier (e.g. gpt-5.4-mini, claude-sonnet-4)",
            "required": True,
            "auto_generate": False,
            "default_value": "gpt-5.4-mini",
            "value": "gpt-5.4-mini",
        },
        {
            "name": "DOH_LLM_BASE_URL",
            "category": "config",
            "description": "Provider API base URL (required for custom provider, empty otherwise)",
            "required": False,
            "auto_generate": False,
            "default_value": "https://api.openai.com/v1",
            "value": "https://api.openai.com/v1",
        },
        {
            "name": "HERMES_WEBUI_PASSWORD",
            "category": "secret",
            "description": "WebUI access password",
            "required": True,
            "auto_generate": False,
            "default_value": None,
            "value": "mysquirrel",
        },
        {
            "name": "ANTHROPIC_API_KEY",
            "category": "secret",
            "description": "Anthropic API key for Claude models",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "OPENAI_API_KEY",
            "category": "secret",
            "description": "OpenAI API key (alternative provider)",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "OPENROUTER_API_KEY",
            "category": "secret",
            "description": "OpenRouter API key for multi-model access (200+ models)",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "TAVILY_API_KEY",
            "category": "secret",
            "description": "Tavily API key for web search capability",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "SLACK_APP_TOKEN",
            "category": "secret",
            "description": "Slack app-level token (leave empty to disable Slack)",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "SLACK_BOT_TOKEN",
            "category": "secret",
            "description": "Slack bot token (leave empty to disable Slack)",
            "required": False,
            "auto_generate": False,
            "default_value": None,
            "value": "",
        },
        {
            "name": "SLACK_ALLOW_ALL_USERS",
            "category": "config",
            "description": "Allow all Slack users to interact with the bot (true/false)",
            "required": False,
            "auto_generate": False,
            "default_value": "true",
            "value": "true",
        },
        {
            "name": "SLACK_ALLOWED_USERS",
            "category": "config",
            "description": "Comma-separated Slack Member IDs (overrides allow-all when set)",
            "required": False,
            "auto_generate": False,
            "default_value": "",
            "value": "",
        },
    ],
    "datastore_config": None,
    "efs_config": {"mount_path": "/home/hermeswebui/.hermes", "posix_uid": 1024, "posix_gid": 1024},
    "is_active": True,
}


TEMPLATES = [OPENCLAW_TEMPLATE, HERMES_TEMPLATE]


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
