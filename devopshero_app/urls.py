from django.urls import path
from . import views

urlpatterns = [
    # Health check for ALB/ECS
    path("health/", views.health_check, name="health_check"),
    
    path("", views.landing, name="landing"),
    path("waitlist/signup/", views.waitlist_signup, name="waitlist_signup"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("workspaces/", views.workspaces, name="workspaces"),
    path("workspaces/create/", views.workspace_create, name="workspace_create"),
    path("workspaces/<slug:workspace_slug>/", views.workspace_detail, name="workspace_detail"),
    path("apps/<slug:app_slug>/", views.app_detail, name="app_detail"),
    path("apps/<slug:app_slug>/deployments/<uuid:deployment_id>/teardown/", views.app_deployment_teardown, name="app_deployment_teardown"),
    path("apps/<slug:app_slug>/deployments/<uuid:deployment_id>/redeploy/", views.app_deployment_redeploy, name="app_deployment_redeploy"),
    path("apps/<slug:app_slug>/deployments/<uuid:deployment_id>/status/", views.app_deployment_status, name="app_deployment_status"),
    path("apps/<slug:app_slug>/deployments/<uuid:deployment_id>/teardown-confirm/", views.app_teardown_confirm, name="app_teardown_confirm"),
    path("environments/", views.environments, name="environments"),
    path("environments/<slug:environment_slug>/", views.environment_detail, name="environment_detail"),
    path("security/", views.security, name="security"),
    path("security/permissions/editor/", views.security_permissions_editor, name="security_permissions_editor"),
    path("security/permissions/<uuid:permission_request_id>/apply/", views.security_permissions_editor_apply, name="security_permissions_editor_apply"),
    path("security/permissions/<uuid:permission_request_id>/update-statement/", views.security_permissions_editor_update_statement, name="security_permissions_editor_update_statement"),
    path("security/permissions/<uuid:permission_request_id>/refresh-resources/", views.security_permissions_editor_refresh_resources, name="security_permissions_editor_refresh_resources"),
    path("security/permissions/<uuid:permission_request_id>/service-group/", views.security_permissions_editor_service_group, name="security_permissions_editor_service_group"),
    path("settings/", views.settings, name="settings"),
    path("settings/organization/", views.settings_organization, name="settings_organization"),
    path("settings/members/", views.settings_members, name="settings_members"),
    path("settings/aws-accounts/", views.settings_aws_accounts, name="settings_aws_accounts"),
    path("settings/aws-accounts/add/", views.settings_aws_accounts_add, name="settings_aws_accounts_add"),
    path("settings/billing/", views.settings_billing, name="settings_billing"),
    path("settings/git-integrations/", views.settings_git_integrations, name="settings_git_integrations"),
    path("random-quote/", views.random_quote, name="random_quote"),
    path("switch-organization/", views.switch_organization, name="switch_organization"),
    
    # Authentication
    path("auth/login/", views.auth_login, name="login"),
    path("auth/callback/", views.auth_callback, name="auth_callback"),
    path("auth/logout/", views.auth_logout, name="logout"),
    
    # Onboarding
    path("onboarding/", views.onboarding, name="onboarding"),
    
    # GitHub App OAuth
    path("github/connect", views.github_connect, name="github_connect"),
    path("github/callback", views.github_callback, name="github_callback"),
    path("github/setup", views.github_callback, name="github_setup"),  # Setup URL uses same handler

    # API endpoints
    #  - aws_install_account_callback: called by Lambda after customer deploys the CloudFormation stack, not browsers
    path("api/aws/install-account-callback", views.aws_install_account_callback, name="aws_install_account_callback"),
    #  - github_webhook: receives push/installation events from GitHub
    path("api/github/webhook", views.github_webhook, name="github_webhook"),

    # Chat / Agent
    path("chat/app_deploy/<slug:workspace_slug>/<str:repo_owner>/<str:repo_name>/", views.chat_app_deploy, name="chat_app_deploy_with_owner"),
    path("chat/app_deploy/<slug:workspace_slug>/<str:repo_name>/", views.chat_app_deploy, name="chat_app_deploy"),
    path("chat/", views.chat_list, name="chat_list"),
    path("chat/new/", views.chat_new, name="chat_new"),
    path("chat/<uuid:conversation_id>/", views.chat_view, name="chat_view"),
    path("chat/<uuid:conversation_id>/send/", views.chat_send, name="chat_send"),
    path("chat/<uuid:conversation_id>/stream/", views.chat_stream, name="chat_stream"),
    path("chat/<uuid:conversation_id>/messages/", views.chat_messages, name="chat_messages"),
    path("chat/<uuid:conversation_id>/close/", views.chat_close, name="chat_close"),
    path("chat/<uuid:conversation_id>/fork/", views.chat_fork, name="chat_fork"),
]
