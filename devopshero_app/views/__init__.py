from .landing import landing
from .dashboard import dashboard
from .workspaces import workspaces, workspace_detail
from .apps import apps
from .datastores import datastores
from .security import security
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
from .api import aws_install_account_callback
from .github import github_connect, github_callback, github_webhook
from .chat import (
    chat_list,
    chat_new,
    chat_view,
    chat_send,
    chat_stream,
    chat_messages,
    chat_close,
)

__all__ = [
    "landing",
    "dashboard",
    "workspaces",
    "workspace_detail",
    "apps",
    "datastores",
    "security",
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
    "github_connect",
    "github_callback",
    "github_webhook",
    "chat_list",
    "chat_new",
    "chat_view",
    "chat_send",
    "chat_stream",
    "chat_messages",
    "chat_close",
]

