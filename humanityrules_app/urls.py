from django.urls import path

from . import views

urlpatterns = [
    # Health check for ALB/ECS
    path("health/", views.health_check, name="health_check"),

    # App-detail cost panel (HTMX fragment; see docs/app_cost_tracking_design.md)
    path("apps/<slug:app_slug>/cost-panel/", views.app_cost_panel, name="app_cost_panel"),

    path("", views.landing, name="landing"),
    path("devopshero-ai/", views.devopshero_landing, name="devopshero_landing"),
    path("waitlist/signup/", views.waitlist_signup, name="waitlist_signup"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("workspaces/", views.workspaces, name="workspaces"),
    path("workspaces/create/", views.workspace_create, name="workspace_create"),
    path("workspaces/<slug:workspace_slug>/", views.workspace_detail, name="workspace_detail"),
    path("workspaces/<slug:workspace_slug>/tags/add/", views.workspace_tag_add, name="workspace_tag_add"),
    path("workspaces/<slug:workspace_slug>/tags/<uuid:tag_id>/remove/", views.workspace_tag_remove, name="workspace_tag_remove"),
    path("workspaces/<slug:workspace_slug>/tags/save/", views.workspace_tags_save, name="workspace_tags_save"),
    path("apps/<slug:app_slug>/", views.app_detail, name="app_detail"),
    path("apps/<slug:app_slug>/tags/add/", views.app_tag_add, name="app_tag_add"),
    path("apps/<slug:app_slug>/tags/<uuid:tag_id>/remove/", views.app_tag_remove, name="app_tag_remove"),
    path("apps/<slug:app_slug>/tags/save/", views.app_tags_save, name="app_tags_save"),

    path("deploy/new/<slug:workspace_slug>/<uuid:repo_id>/", views.deployment_editor_new, name="deployment_editor_new"),
    path("deploy/fork/<uuid:conversation_id>/", views.deployment_editor_fork, name="deployment_editor_fork"),
    path("deploy/from-template/", views.template_deploy_picker, name="template_deploy_picker"),
    path("deploy/from-template/<slug:template_slug>/", views.template_deploy_form, name="template_deploy_form"),
    path("deploy/<slug:app_slug>/", views.deployment_editor, name="deployment_editor"),
    path("deploy/<slug:app_slug>/reset/", views.deployment_editor_reset, name="deployment_editor_reset"),
    path("deploy/<slug:app_slug>/app-section/", views.deployment_editor_app_section, name="deployment_editor_app_section"),
    path("deploy/<slug:app_slug>/blueprint-section/", views.deployment_editor_blueprint_section, name="deployment_editor_blueprint_section"),

    path("blueprints/<uuid:blueprint_id>/row-status/", views.blueprint_row_status, name="blueprint_row_status"),
    path("apps/<slug:app_slug>/deployments/<uuid:deployment_id>/teardown/", views.app_deployment_teardown, name="app_deployment_teardown"),
    path("apps/<slug:app_slug>/deployments/<uuid:deployment_id>/redeploy/", views.app_deployment_redeploy, name="app_deployment_redeploy"),
    path("apps/<slug:app_slug>/deployments/<uuid:deployment_id>/status/", views.app_deployment_status, name="app_deployment_status"),
    path("apps/<slug:app_slug>/deployment-log/", views.app_deployment_log, name="app_deployment_log"),
    path("apps/<slug:app_slug>/card-status/", views.app_card_status, name="app_card_status"),
    path("apps/<slug:app_slug>/deployments/<uuid:deployment_id>/teardown-confirm/", views.app_teardown_confirm, name="app_teardown_confirm"),
    path("apps/<slug:app_slug>/remove-confirm/", views.app_remove_confirm, name="app_remove_confirm"),
    path("apps/<slug:app_slug>/remove/", views.app_remove, name="app_remove"),

    # Environments
    path("environments/", views.environments, name="environments"),
    path("environments/setup/", views.environment_setup_form, name="environment_setup_form"),
    path("environments/new/", views.environment_editor_new, name="environment_editor_new"),
    path("environments/<uuid:environment_id>/setup/", views.environment_editor, name="environment_editor"),
    path("environments/<uuid:environment_id>/setup/section/", views.environment_editor_environment_section, name="environment_editor_environment_section"),
    path("environments/<uuid:environment_id>/setup/reset/", views.environment_editor_reset, name="environment_editor_reset"),
    path("environments/<uuid:environment_id>/", views.environment_detail, name="environment_detail"),
    path("environments/<uuid:environment_id>/provisioning-log/", views.environment_provisioning_log, name="environment_provisioning_log"),
    path("environments/<uuid:environment_id>/status/", views.environment_status, name="environment_status"),
    path("environments/<uuid:environment_id>/retry/", views.environment_retry, name="environment_retry"),
    path("environments/<uuid:environment_id>/teardown-confirm/", views.environment_teardown_confirm, name="environment_teardown_confirm"),
    path("environments/<uuid:environment_id>/teardown/", views.environment_teardown, name="environment_teardown"),
    path("environments/<uuid:environment_id>/tags/add/", views.environment_tag_add, name="environment_tag_add"),
    path("environments/<uuid:environment_id>/tags/<uuid:tag_id>/remove/", views.environment_tag_remove, name="environment_tag_remove"),
    path("environments/<uuid:environment_id>/tags/save/", views.environment_tags_save, name="environment_tags_save"),

    # Security
    path("security/hub/", views.security_hub, name="security_hub"),
    path("security/permissions/editor/", views.security_permissions_editor, name="security_permissions_editor"),
    path("security/permissions/<uuid:app_permission_request_id>/apply/", views.security_permissions_editor_apply, name="security_permissions_editor_apply"),
    path("security/permissions/<uuid:app_permission_request_id>/cancel/", views.security_permissions_editor_cancel, name="security_permissions_editor_cancel"),
    path("security/permissions/<uuid:app_permission_request_id>/update-statement/", views.security_permissions_editor_update_statement, name="security_permissions_editor_update_statement"),
    path("security/permissions/<uuid:app_permission_request_id>/description/", views.security_permissions_editor_description, name="security_permissions_editor_description"),
    path("security/permissions/<uuid:app_permission_request_id>/update-description/", views.security_permissions_editor_update_description, name="security_permissions_editor_update_description"),
    path("security/permissions/<uuid:app_permission_request_id>/refresh-resources/", views.security_permissions_editor_refresh_resources, name="security_permissions_editor_refresh_resources"),
    path("security/permissions/<uuid:app_permission_request_id>/service-group/", views.security_permissions_editor_service_group, name="security_permissions_editor_service_group"),
    path("security/permissions/<uuid:app_permission_request_id>/statements/", views.security_permissions_statements, name="security_permissions_statements"),
    # ABAC security
    path("security/people/", views.security_people, name="security_people"),
    path("security/people/default-role/", views.security_people_default_role, name="security_people_default_role"),
    path("security/people/<uuid:user_id>/", views.security_people_detail, name="security_people_detail"),
    path("security/people/<uuid:user_id>/attributes/add/", views.security_people_attribute_add, name="security_people_attribute_add"),
    path("security/people/<uuid:user_id>/attributes/<uuid:attribute_id>/remove/", views.security_people_attribute_remove, name="security_people_attribute_remove"),
    path("security/people/<uuid:user_id>/groups/add/", views.security_people_group_add, name="security_people_group_add"),
    path("security/people/<uuid:user_id>/groups/<uuid:membership_id>/remove/", views.security_people_group_remove, name="security_people_group_remove"),
    path("security/groups/", views.security_groups, name="security_groups"),
    path("security/groups/create/", views.security_group_create, name="security_group_create"),
    path("security/groups/<uuid:group_id>/", views.security_group_detail, name="security_group_detail"),
    path("security/groups/<uuid:group_id>/delete-confirm/", views.security_group_delete_confirm, name="security_group_delete_confirm"),
    path("security/groups/<uuid:group_id>/delete/", views.security_group_delete, name="security_group_delete"),
    path("security/groups/<uuid:group_id>/attributes/add/", views.security_group_attribute_add, name="security_group_attribute_add"),
    path("security/groups/<uuid:group_id>/attributes/<uuid:attribute_id>/remove/", views.security_group_attribute_remove, name="security_group_attribute_remove"),
    path("security/groups/<uuid:group_id>/members/add/", views.security_group_member_add, name="security_group_member_add"),
    path("security/groups/<uuid:group_id>/members/<uuid:membership_id>/remove/", views.security_group_member_remove, name="security_group_member_remove"),
    path("security/policies/", views.security_policies, name="security_policies"),
    path("security/policies/create/", views.security_policy_create, name="security_policy_create"),
    path("security/policies/<uuid:policy_id>/", views.security_policy_detail, name="security_policy_detail"),
    path("security/policies/<uuid:policy_id>/delete-confirm/", views.security_policy_delete_confirm, name="security_policy_delete_confirm"),
    path("security/policies/<uuid:policy_id>/delete/", views.security_policy_delete, name="security_policy_delete"),
    path("settings/", views.settings, name="settings"),
    path("settings/personal/", views.settings_personal, name="settings_personal"),
    path("settings/organization/", views.settings_organization, name="settings_organization"),
    path("settings/billing/", views.settings_billing, name="settings_billing"),

    path("random-quote/", views.random_quote, name="random_quote"),
    path("switch-organization/", views.switch_organization, name="switch_organization"),

    # Authentication
    path("auth/dev-login/", views.dev_login, name="dev_login"),
    path("auth/login/", views.auth_login, name="login"),
    path("auth/callback/", views.auth_callback, name="auth_callback"),
    path("auth/logout/", views.auth_logout, name="logout"),
    path("oidc/login/", views.oidc_login, name="oidc_login"),
    path("oidc/callback/", views.oidc_callback, name="oidc_callback"),

    # Env-SSO: control plane owns the OAuth dance for every customer env
    path("auth/env-start", views.env_start, name="env_start"),
    path("auth/env-callback", views.env_callback, name="env_callback"),
    path(".well-known/jwks.json", views.env_jwks, name="env_jwks"),

    # Onboarding
    path("onboarding/", views.onboarding, name="onboarding"),

    # Organization invites
    path("invite/<uuid:token>/", views.accept_invite, name="invite_accept"),
    path("settings/invites/create/", views.create_invite, name="invite_create"),
    path("settings/invites/<uuid:invite_id>/revoke/", views.revoke_invite, name="invite_revoke"),

    # Integrations — organization-level (org admin configures once at /integrations/;
    # persists AWSAccount / IntegrationGitProvider keyed by organization).
    path("integrations/", views.integrations_root, name="integrations_root"),
    path("integrations/org/aws-accounts/", views.integrations_org_aws_accounts, name="integrations_org_aws_accounts"),
    path("integrations/org/aws-accounts/add/", views.integrations_org_aws_accounts_add, name="integrations_org_aws_accounts_add"),
    path("integrations/org/aws-accounts/<uuid:account_id>/status/", views.integrations_org_aws_accounts_status, name="integrations_org_aws_accounts_status"),
    path("integrations/org/aws-accounts/<uuid:account_id>/edit/", views.integrations_org_aws_accounts_edit, name="integrations_org_aws_accounts_edit"),
    path("integrations/org/aws-accounts/<uuid:account_id>/disconnect-confirm/", views.integrations_org_aws_accounts_disconnect_confirm, name="integrations_org_aws_accounts_disconnect_confirm"),
    path("integrations/org/aws-accounts/<uuid:account_id>/disconnect/", views.integrations_org_aws_accounts_disconnect, name="integrations_org_aws_accounts_disconnect"),

    # GitHub integration: org-level (org admin configures once at /integrations/;
    path("integrations/org/github/", views.integrations_org_github, name="integrations_org_github"),
    # GitHub App install flow: launched from the GitHub tab; binds installation_id to the org.
    path("integrations/org/github/connect/", views.integrations_org_github_connect, name="integrations_org_github_connect"),
    path("integrations/org/github/callback/", views.integrations_org_github_callback, name="integrations_org_github_callback"),
    path("integrations/org/github/setup/", views.integrations_org_github_setup, name="integrations_org_github_setup"),
    path("integrations/org/github/select-installation/", views.integrations_org_github_select_installation, name="integrations_org_github_select_installation"),

    # Shared provider keys: org admin provisions one key (OpenRouter/OpenAI/Anthropic)
    # and shares it with everyone / a workspace / a user. Persists IntegrationSharedCredential.
    path("integrations/org/provider-keys/", views.integrations_org_shared_keys, name="integrations_org_shared_keys"),
    path("integrations/org/provider-keys/add/", views.integrations_org_shared_keys_add, name="integrations_org_shared_keys_add"),
    path("integrations/org/provider-keys/<uuid:credential_id>/edit/", views.integrations_org_shared_keys_edit, name="integrations_org_shared_keys_edit"),
    path("integrations/org/provider-keys/<uuid:credential_id>/delete/", views.integrations_org_shared_keys_delete, name="integrations_org_shared_keys_delete"),

    # Integrations — per-user (each user grants OAuth from inside a deployed app).
    # Persists IntegrationUserCredential keyed by (owner_user, environment, app_slug, provider).
    # No long-lived secrets ever reach the customer env.
    path("integrations/user/google/start/", views.integrations_user_google_start, name="integrations_user_google_start"),
    path("integrations/user/google/callback/", views.integrations_user_google_callback, name="integrations_user_google_callback"),
    path("integrations/user/github/start/", views.integrations_user_github_start, name="integrations_user_github_start"),
    path("integrations/user/github/callback/", views.integrations_user_github_callback, name="integrations_user_github_callback"),
    path("integrations/user/x/start/", views.integrations_user_x_start, name="integrations_user_x_start"),
    path("integrations/user/x/callback/", views.integrations_user_x_callback, name="integrations_user_x_callback"),
    # Connect is the browser OAuth round-trip above (start → provider → callback).
    # Disconnect is broker-only: the Hermes WebUI POSTs to /__humr_broker/integrations/tls_intercept/{slug}/disconnect,
    # which forwards here with the env bearer — see the disconnect handler under api/integrations below.

    # API endpoints (view implementations live under views/integrations/ or views/pdp.py)
    path("api/aws/install-account-callback", views.aws_install_account_callback, name="aws_install_account_callback"),
    #  - github_webhook: receives push/installation events from GitHub
    path("api/github/webhook", views.github_webhook, name="github_webhook"),
    #  - pdp_evaluate: called by policy proxies to authorize each request against ABAC
    path("api/pdp/evaluate", views.pdp_evaluate, name="pdp_evaluate"),
    
    #  - integrations_tokens_batch: the env-resident broker's single refresh endpoint, both for Refresh-all/bootstrap and for slug-targeted refresh after connect/disconnect
    path("api/integrations/tokens", views.integrations_tokens_batch, name="integrations_tokens_batch"),
    #  - integrations_credential_setup_session / submit: broker-assisted, browser-direct vault flows for paste-style credentials
    path("api/integrations/credentials/setup-session", views.integrations_credential_setup_session, name="integrations_credential_setup_session"),
    path("api/integrations/credentials/submit", views.integrations_credential_submit, name="integrations_credential_submit"),
    #  - integrations_credential_poll: browser-direct poll for link-driven vault setups (e.g. Telegram managed bots)
    path("api/integrations/credentials/poll", views.integrations_credential_poll, name="integrations_credential_poll"),
    #  - disconnect: one handler for every provider kind — deletes the IntegrationUserCredential row and, for
    #    OAuth providers, best-effort revokes upstream. The broker posts all disconnects here.
    path("api/integrations/credentials/disconnect", views.integrations_credential_disconnect, name="integrations_credential_disconnect"),
    #  - device-complete: the broker posts device-flow refresh tokens here after the user approves; HUMR validates + stores them
    path("api/integrations/credentials/<slug:provider>/device-complete", views.integrations_device_complete, name="integrations_device_complete"),

    #  - Merge.dev Agent Handler: env-resident components (Hermes broker / MCP aggregator) reach Merge through these. Tenant-wide Merge API key lives only on HUMR.
    path("api/integrations/merge/ensure-registered-user", views.integrations_merge_ensure_registered_user, name="integrations_merge_ensure_registered_user"),
    path("api/integrations/merge/link-token", views.integrations_merge_link_token, name="integrations_merge_link_token"),
    path("api/integrations/merge/connectors", views.integrations_merge_connectors, name="integrations_merge_connectors"),
    path("api/integrations/merge/connector-status", views.integrations_merge_connector_status, name="integrations_merge_connector_status"),
    path("api/integrations/merge/disconnect", views.integrations_merge_disconnect, name="integrations_merge_disconnect"),
    path("api/integrations/merge/mcp", views.integrations_merge_mcp, name="integrations_merge_mcp"),

    #  - Permissions editor (self-referential): the env-resident humr_broker relays the Hermes WebUI's
    #    /permissions/* calls here with the env bearer. Target (app, environment) is resolved from the
    #    bearer + owner_username/app_slug body, never a parameter. See docs/permissions_broker_design.md.
    path("api/permissions/draft", views.permissions_draft, name="permissions_draft"),
    path("api/permissions/draft/statement", views.permissions_statement, name="permissions_statement"),
    path("api/permissions/draft/description", views.permissions_description, name="permissions_description"),
    path("api/permissions/draft/cancel", views.permissions_cancel, name="permissions_cancel"),
    path("api/permissions/draft/apply", views.permissions_apply, name="permissions_apply"),
    path("api/permissions/draft/refresh-resources", views.permissions_refresh_resources, name="permissions_refresh_resources"),
    path("api/permissions/resources", views.permissions_resources, name="permissions_resources"),
    path("api/permissions/service-catalog", views.permissions_service_catalog, name="permissions_service_catalog"),

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
    path("chat/<uuid:conversation_id>/title/", views.chat_conversation_title, name="chat_conversation_title"),
    path("chat/<uuid:conversation_id>/cost/", views.chat_conversation_cost, name="chat_conversation_cost"),
]
