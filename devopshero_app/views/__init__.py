from .landing import landing
from .dashboard import dashboard
from .workspaces import workspaces, workspace_detail, workspace_create
from .apps import app_detail, app_deployment_teardown, app_deployment_redeploy, app_deployment_status, app_teardown_confirm
from .environments import environments, environment_detail
from .security import (
    security,
    security_permissions_editor,
    security_permissions_editor_apply,
    security_permissions_editor_statements,
)
from .settings import (
    settings,
    settings_organization,
    settings_members,
    settings_aws_accounts,
    settings_aws_accounts_add,
    settings_billing,
    settings_git_integrations,
)
from .partials import random_quote, switch_organization
from .auth import auth_login, auth_callback, auth_logout
from .onboarding import onboarding
from .api import aws_install_account_callback, health_check
from .github import github_connect, github_callback, github_webhook
from .chat import (
    chat_app_deploy,
    chat_list,
    chat_new,
    chat_view,
    chat_send,
    chat_stream,
    chat_messages,
    chat_close,
    chat_fork,
)
from .waitlist_views import waitlist_signup

__all__ = [
    "landing",
    "dashboard",
    "workspaces",
    "workspace_detail",
    "workspace_create",
    "app_detail",
    "app_deployment_teardown",
    "app_deployment_redeploy",
    "app_deployment_status",
    "app_teardown_confirm",
    "environments",
    "environment_detail",
    "security",
    "security_permissions_editor",
    "security_permissions_editor_apply",
    "security_permissions_editor_statements",
    "settings",
    "settings_organization",
    "settings_members",
    "settings_aws_accounts",
    "settings_aws_accounts_add",
    "settings_billing",
    "settings_git_integrations",
    "random_quote",
    "switch_organization",
    "auth_login",
    "auth_callback",
    "auth_logout",
    "onboarding",
    "aws_install_account_callback",
    "health_check",
    "github_connect",
    "github_callback",
    "github_webhook",
    "chat_app_deploy",
    "chat_list",
    "chat_new",
    "chat_view",
    "chat_send",
    "chat_stream",
    "chat_messages",
    "chat_close",
    "chat_fork",
    "waitlist_signup",
]
