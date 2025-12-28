from .landing import landing
from .dashboard import dashboard
from .workspaces import workspaces
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
)
from .partials import random_quote, switch_organization
from .auth import auth_login, auth_callback, auth_logout
from .onboarding import onboarding
from .api import aws_install_account_callback

__all__ = [
    "landing",
    "dashboard",
    "workspaces",
    "apps",
    "datastores",
    "security",
    "settings",
    "settings_organization",
    "settings_members",
    "settings_aws_accounts",
    "settings_aws_accounts_add",
    "settings_billing",
    "random_quote",
    "switch_organization",
    "auth_login",
    "auth_callback",
    "auth_logout",
    "onboarding",
    "aws_install_account_callback",
]

