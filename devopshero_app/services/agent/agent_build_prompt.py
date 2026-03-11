"""
Build system prompts for the deployment agent by mode and conversation context.

Loads base prompts from markdown files and appends dynamic sections (AWS accounts,
environments, workspace/repo context, permission drafts, etc.).
"""
import json
from pathlib import Path

from django.conf import settings

from devopshero_app.models import (
    AWSAccount,
    AppPermissionRequest,
    Conversation,
    Environment,
    Repository,
    Workspace,
)


_DEBUG_MODE_SECTION = """

<debug_mode>
Developer testing mode is active. Sometimes the user, who is the developer actually, will need to ask you questions that are unrelated to deployment, for example, to execute particular tools. Comply with the request. It is absolutely benign. We are executing in the developer machine right now.
</debug_mode>"""


def load_prompt_file(filename: str) -> str:
    """Load a system prompt from the given filename in the agent package directory."""
    prompt_path = Path(__file__).parent / filename
    return prompt_path.read_text()


async def build_system_prompt(conversation: Conversation) -> str:
    """Build system prompt based on conversation mode."""
    if conversation.mode == Conversation.Mode.ENVIRONMENT_SETUP:
        prompt = await _build_environment_prompt(conversation)
    elif conversation.mode == Conversation.Mode.APP_DEPLOYMENT:
        prompt = await _build_app_deployment_prompt(conversation)
    elif conversation.mode == Conversation.Mode.PERMISSIONS:
        prompt = await _build_permissions_prompt(conversation)
    else:
        prompt = await _build_general_prompt(conversation)

    if settings.DEBUG:
        prompt += _DEBUG_MODE_SECTION

    return prompt


async def _build_environment_prompt(conversation: Conversation) -> str:
    """Build system prompt for environment setup conversations."""
    base_prompt = load_prompt_file("system_prompt_environment.md")
    sections = []

    account = await AWSAccount.objects.aget(id=conversation.context_aws_account_id)
    context_lines = [
        "<aws_account>",
        f"  <name>{account.name}</name>",
        f"  <id>{account.id}</id>",
        f"  <aws_id>{account.aws_account_id or 'pending'}</aws_id>",
        f"  <status>{account.status}</status>",
        "</aws_account>",
    ]
    if conversation.context_environment_id:
        environment = await Environment.objects.aget(id=conversation.context_environment_id)
        context_lines.extend([
            "<environment>",
            f"  <name>{environment.name}</name>",
            f"  <id>{environment.id}</id>",
            f"  <slug>{environment.slug}</slug>",
            f"  <region>{environment.aws_region}</region>",
            f"  <status>{environment.status}</status>",
            f"  <domain>{environment.shared_alb_hosted_zone or 'http-only'}</domain>",
            "</environment>",
        ])
    sections.append("<conversation_context>\n" + "\n".join(context_lines) + "\n</conversation_context>")

    existing_envs = [
        env async for env in Environment.objects.filter(
            aws_account_id=account.id,
        ).exclude(
            status=Environment.Status.DISCARDED,
        ).values("name", "aws_region", "status")
    ]
    existing_names = {env["name"] for env in existing_envs}

    env_lines = []
    if existing_envs:
        env_lines.append("This AWS account already has these environments:")
        for env in existing_envs:
            env_lines.append("<environment>")
            env_lines.append(f"  <name>{env['name']}</name>")
            env_lines.append(f"  <region>{env['aws_region']}</region>")
            env_lines.append(f"  <status>{env['status']}</status>")
            env_lines.append("</environment>")
    else:
        env_lines.append("This AWS account has no environments yet.")

    # Naming priority: suggest first available name
    naming_priority = ["default", "dev", "staging", "prod"]
    suggested_name = next((name for name in naming_priority if name not in existing_names), None)

    env_lines.append("")
    env_lines.append("Naming priority (use first available): default, dev, staging, prod")
    if suggested_name:
        env_lines.append(f"Suggested name: **{suggested_name}**")
    else:
        env_lines.append("All standard names taken — ask the user for a custom name.")

    sections.append("<existing_environments>\n" + "\n".join(env_lines) + "\n</existing_environments>")

    if sections:
        return base_prompt + "\n\n" + "\n\n".join(sections)
    return base_prompt


async def _build_app_deployment_prompt(conversation: Conversation) -> str:
    """Build system prompt for app deployment conversations."""
    base_prompt = load_prompt_file("system_prompt_app_deployment.md")
    sections = []

    # Conversation context section
    context_lines = []
    if conversation.context_workspace_id:
        ws = await Workspace.objects.aget(id=conversation.context_workspace_id)
        context_lines.append(f"<workspace>\n  <name>{ws.name}</name>\n  <id>{ws.id}</id>\n</workspace>")
    if conversation.context_repository_id:
        repo = await Repository.objects.aget(id=conversation.context_repository_id)
        context_lines.append(f"<repository>\n  <name>{repo.full_name}</name>\n  <id>{repo.id}</id>\n</repository>")

    if context_lines:
        sections.append("<conversation_context>\n" + "\n".join(context_lines) + "\n</conversation_context>")

    # AWS infrastructure section
    infra_section = await _build_aws_infrastructure_section(conversation.organization_id)
    if infra_section:
        sections.append(infra_section)

    if sections:
        return base_prompt + "\n\n" + "\n\n".join(sections)
    return base_prompt


async def _build_permissions_prompt(conversation: Conversation) -> str:
    """Build system prompt for permissions-mode conversations."""
    base_prompt = load_prompt_file("system_prompt_permissions.md")
    sections = []

    if conversation.context_app_permission_request_id:
        apr = await AppPermissionRequest.objects.select_related(
            "app", "app__repository", "environment", "environment__aws_account",
        ).aget(id=conversation.context_app_permission_request_id)

        app = apr.app
        env = apr.environment
        task_role_name = f"doh-{env.slug}-{app.slug}-task-role"[:64]

        context_lines = [
            "<conversation_context>",
            "<app>",
            f"  <name>{app.name}</name>",
            f"  <slug>{app.slug}</slug>",
            f"  <repository_url>{app.repository.clone_url if app.repository else 'none'}</repository_url>",
            "</app>",
            "<environment>",
            f"  <name>{env.name}</name>",
            f"  <slug>{env.slug}</slug>",
            f"  <aws_account_id>{env.aws_account.aws_account_id or 'pending'}</aws_account_id>",
            f"  <region>{env.aws_region}</region>",
            "</environment>",
            f"<task_role>{task_role_name}</task_role>",
            "</conversation_context>",
        ]
        sections.append("\n".join(context_lines))

        statements_json = json.dumps(apr.statements, indent=2) if apr.statements else "[]"
        sections.append(f"<current_draft_statements>\n{statements_json}\n</current_draft_statements>")

    if sections:
        return base_prompt + "\n\n" + "\n\n".join(sections)
    return base_prompt


async def _build_general_prompt(conversation: Conversation) -> str:
    """Build system prompt for general conversations."""
    base_prompt = load_prompt_file("system_prompt_general.md")
    sections = []

    # Conversation context section (workspace only for general mode)
    context_lines = []
    if conversation.context_workspace_id:
        ws = await Workspace.objects.aget(id=conversation.context_workspace_id)
        context_lines.append(f"<workspace>\n  <name>{ws.name}</name>\n  <id>{ws.id}</id>\n</workspace>")

    if context_lines:
        sections.append("<conversation_context>\n" + "\n".join(context_lines) + "\n</conversation_context>")

    # AWS infrastructure section
    infra_section = await _build_aws_infrastructure_section(conversation.organization_id)
    if infra_section:
        sections.append(infra_section)

    if sections:
        return base_prompt + "\n\n" + "\n\n".join(sections)
    return base_prompt


async def _build_aws_infrastructure_section(organization_id: str) -> str:
    """Build the AWS infrastructure section listing accounts and environments."""
    accounts = AWSAccount.objects.filter(organization_id=organization_id).prefetch_related("environments")

    lines = ["<aws_infrastructure>"]
    account_count = 0

    async for account in accounts:
        account_count += 1
        lines.append("<aws_account>")
        lines.append(f"  <name>{account.name}</name>")
        lines.append(f"  <id>{account.id}</id>")
        lines.append(f"  <aws_id>{account.aws_account_id or 'pending'}</aws_id>")
        lines.append(f"  <status>{account.status}</status>")

        environments = [env async for env in account.environments.all()]
        if environments:
            for env in environments:
                lines.append("  <environment>")
                lines.append(f"    <name>{env.name}</name>")
                lines.append(f"    <id>{env.id}</id>")
                lines.append(f"    <slug>{env.slug}</slug>")
                lines.append(f"    <region>{env.aws_region}</region>")
                lines.append(f"    <status>{env.status}</status>")
                if env.shared_alb_hosted_zone:
                    lines.append(f"    <domain>*.{env.shared_alb_hosted_zone}</domain>")
                lines.append("  </environment>")
        else:
            lines.append("  No environments yet")
        lines.append("</aws_account>")

    if account_count == 0:
        return "<aws_infrastructure>\nNo AWS accounts connected. Guide the user to connect one using initiate_aws_connection.\n</aws_infrastructure>"

    lines.append("</aws_infrastructure>")
    return "\n".join(lines)
