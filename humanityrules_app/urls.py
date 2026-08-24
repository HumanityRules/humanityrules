"""Map control-plane browser and runtime API URLs to the exported view callables."""

from django.urls import path

from . import views

urlpatterns = [
    # Health check for ALB/ECS
    path("health/", views.health_check, name="health_check"),

    path("", views.landing, name="landing"),
    path("contact/", views.contact, name="contact"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("security/", views.security, name="security"),
    path("team/", views.team, name="team"),
    path("devopshero-ai/", views.devopshero_landing, name="devopshero_landing"),
    path("waitlist/signup/", views.waitlist_signup, name="waitlist_signup"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("random-quote/", views.random_quote, name="random_quote"),
    path("switch-organization/", views.switch_organization, name="switch_organization"),

    # Onboarding
    path("onboarding/", views.onboarding, name="onboarding"),
    path("onboarding/agent/", views.onboarding_agent, name="onboarding_agent"),

    # Organization invites
    path("invite/<uuid:token>/", views.accept_invite, name="invite_accept"),
    path("settings/invites/create/", views.create_invite, name="invite_create"),
    path("settings/invites/<uuid:invite_id>/revoke/", views.revoke_invite, name="invite_revoke"),

    # Workspaces
    path("workspaces/create/", views.workspace_create, name="workspace_create"),
    path("workspaces/<slug:workspace_slug>/", views.workspace_detail, name="workspace_detail"),
    path("workspaces/<slug:workspace_slug>/remove-confirm/", views.workspace_remove_confirm, name="workspace_remove_confirm"),
    path("workspaces/<slug:workspace_slug>/remove/", views.workspace_remove, name="workspace_remove"),
    path("workspaces/<slug:workspace_slug>/tags/save/", views.workspace_tags_save, name="workspace_tags_save"),

    # Apps
    path("apps/<slug:app_slug>/", views.app_detail, name="app_detail"),
    path("apps/<slug:app_slug>/tags/save/", views.app_tags_save, name="app_tags_save"),

    path("deploy/from-template/", views.template_deploy_picker, name="template_deploy_picker"),
    path("deploy/from-template/<slug:template_slug>/", views.template_deploy_form, name="template_deploy_form"),

    path("apps/<slug:app_slug>/deployment-section-status/", views.app_deployment_section_status, name="app_deployment_section_status"),

    path("apps/<slug:app_slug>/teardown/", views.app_deployment_teardown, name="app_deployment_teardown"),
    path("apps/<slug:app_slug>/redeploy/", views.app_deployment_redeploy, name="app_deployment_redeploy"),
    path("apps/<slug:app_slug>/status-row/", views.app_status_row, name="app_status_row"),
    path("apps/<slug:app_slug>/deployment-log/", views.app_deployment_log, name="app_deployment_log"),
    path("apps/<slug:app_slug>/card/", views.app_card, name="app_card"),
    path("apps/<slug:app_slug>/teardown-confirm/", views.app_teardown_confirm, name="app_teardown_confirm"),
    path("apps/<slug:app_slug>/remove/", views.app_remove, name="app_remove"),
    path("apps/<slug:app_slug>/remove-confirm/", views.app_remove_confirm, name="app_remove_confirm"),

    path("apps/<slug:app_slug>/public-access/new", views.webapp_public_access_new, name="webapp_public_access_new"),
    path("apps/<slug:app_slug>/public-access/", views.webapp_public_access_create, name="webapp_public_access_create"),
    path("apps/<slug:app_slug>/public-access/<uuid:grant_id>/", views.webapp_public_access_status, name="webapp_public_access_status"),
    path("apps/<slug:app_slug>/public-access/<uuid:grant_id>/check/", views.webapp_public_access_check, name="webapp_public_access_check"),
    path("apps/<slug:app_slug>/public-access/<uuid:grant_id>/revoke-confirm/", views.webapp_public_access_revoke_confirm, name="webapp_public_access_revoke_confirm"),
    path("apps/<slug:app_slug>/public-access/<uuid:grant_id>/revoke/", views.webapp_public_access_revoke, name="webapp_public_access_revoke"),

    # App-detail cost panel (HTMX fragment; see docs/app_cost_tracking_design.md)
    path("apps/<slug:app_slug>/cost-panel/", views.app_cost_panel, name="app_cost_panel"),

    # Environments
    path("environments/", views.environments, name="environments"),
    path("environments/setup/", views.environment_setup_form, name="environment_setup_form"),
    path("environments/<uuid:environment_id>/", views.environment_detail, name="environment_detail"),
    path("environments/<uuid:environment_id>/provisioning-log/", views.environment_provisioning_log, name="environment_provisioning_log"),
    path("environments/<uuid:environment_id>/status/", views.environment_status, name="environment_status"),
    path("environments/<uuid:environment_id>/retry/", views.environment_retry, name="environment_retry"),
    path("environments/<uuid:environment_id>/teardown-confirm/", views.environment_teardown_confirm, name="environment_teardown_confirm"),
    path("environments/<uuid:environment_id>/teardown/", views.environment_teardown, name="environment_teardown"),
    path("environments/<uuid:environment_id>/tags/save/", views.environment_tags_save, name="environment_tags_save"),

    # Security
    path("security/hub/", views.security_hub, name="security_hub"),
    path("security/permissions/editor/", views.security_permissions_editor, name="security_permissions_editor"),
    path("security/permissions/<uuid:app_permission_request_id>/apply/", views.security_permissions_editor_apply, name="security_permissions_editor_apply"),
    path("security/permissions/<uuid:app_permission_request_id>/cancel/", views.security_permissions_editor_cancel, name="security_permissions_editor_cancel"),
    path("security/permissions/<uuid:app_permission_request_id>/update-statement/", views.security_permissions_editor_update_statement, name="security_permissions_editor_update_statement"),
    path("security/permissions/<uuid:app_permission_request_id>/update-description/", views.security_permissions_editor_update_description, name="security_permissions_editor_update_description"),
    path("security/permissions/<uuid:app_permission_request_id>/refresh-resources/", views.security_permissions_editor_refresh_resources, name="security_permissions_editor_refresh_resources"),
    path("security/permissions/<uuid:app_permission_request_id>/service-group/", views.security_permissions_editor_service_group, name="security_permissions_editor_service_group"),
    
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
    path("settings/billing/checkout", views.settings_billing_checkout, name="settings_billing_checkout"),
    path("settings/billing/portal", views.settings_billing_portal, name="settings_billing_portal"),


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

    # Shared credentials: org admin provisions a key (OpenRouter/OpenAI/Anthropic/Tavily)
    # or connects a device-flow login (Codex/Nous) and shares it with everyone / a workspace /
    # a user — or, for the platform-owner org, all customers. Persists IntegrationSharedCredential
    # (per-org) or PlatformSharedCredential (all customers); see shared_credential_store.
    path("integrations/org/provider-keys/", views.integrations_org_shared_keys, name="integrations_org_shared_keys"),
    path("integrations/org/provider-keys/add/", views.integrations_org_shared_keys_add, name="integrations_org_shared_keys_add"),
    path("integrations/org/provider-keys/<uuid:credential_id>/edit/", views.integrations_org_shared_keys_edit, name="integrations_org_shared_keys_edit"),
    path("integrations/org/provider-keys/<uuid:credential_id>/delete/", views.integrations_org_shared_keys_delete, name="integrations_org_shared_keys_delete"),

    # Device-login sub-steps for the connect-a-login path of the unified dialog (add/edit live on
    # the provider-keys routes above). The control plane drives the handshake; the admin's browser
    # self-polls `poll` until approved. `reconnect` replaces an existing login's refresh token.
    path("integrations/org/shared-logins/poll/", views.integrations_org_shared_login_poll, name="integrations_org_shared_login_poll"),
    path("integrations/org/shared-logins/cancel/", views.integrations_org_shared_login_cancel, name="integrations_org_shared_login_cancel"),
    path("integrations/org/shared-logins/<uuid:credential_id>/reconnect/", views.integrations_org_shared_login_reconnect, name="integrations_org_shared_login_reconnect"),

    # Integrations — per-user (each user grants OAuth from inside a deployed app).
    # Persists IntegrationUserCredential keyed by (owner_user, environment, app_slug, provider).
    # No long-lived secrets ever reach the customer env.
    path("integrations/user/google/start/", views.integrations_user_google_start, name="integrations_user_google_start"),
    path("integrations/user/google/narrow/", views.integrations_user_google_narrow, name="integrations_user_google_narrow"),
    path("integrations/user/google/callback/", views.integrations_user_google_callback, name="integrations_user_google_callback"),
    path("integrations/user/github/start/", views.integrations_user_github_start, name="integrations_user_github_start"),
    path("integrations/user/github/callback/", views.integrations_user_github_callback, name="integrations_user_github_callback"),
    path("integrations/user/x/start/", views.integrations_user_x_start, name="integrations_user_x_start"),
    path("integrations/user/x/callback/", views.integrations_user_x_callback, name="integrations_user_x_callback"),
    # Connect is the browser OAuth round-trip above (start → provider → callback).
    # Disconnect is broker-only: the Hermes WebUI POSTs to /__humr_broker/integrations/tls_intercept/{slug}/disconnect,
    # which forwards here with the env bearer — see the disconnect handler under api/integrations below.
    
    #  - integrations_tokens_batch: the env-resident broker's single refresh endpoint, both for Refresh-all/bootstrap and for slug-targeted refresh after connect/disconnect
    path("api/integrations/tokens", views.integrations_tokens_batch, name="integrations_tokens_batch"),

    #  - integrations_credential_setup_session & submit: broker-assisted, browser-direct vault flows for API Keys (paste-style) credentials
    path("api/integrations/credentials/setup-session", views.integrations_credential_setup_session, name="integrations_credential_setup_session"),
    path("api/integrations/credentials/submit", views.integrations_credential_submit, name="integrations_credential_submit"),

    #  - integrations_credential_poll: browser-direct poll for link-driven vault setups (e.g. Telegram managed bots)
    path("api/integrations/credentials/poll", views.integrations_credential_poll, name="integrations_credential_poll"),
    #  - device-complete: the broker posts device-flow refresh tokens here after the user approves; HUMR validates + stores them
    path("api/integrations/credentials/<slug:provider>/device-complete", views.integrations_device_complete, name="integrations_device_complete"),

    #  - disconnect: one handler for every provider kind — deletes the IntegrationUserCredential row and, for
    #    OAuth providers, best-effort revokes upstream. The broker posts all disconnects here.
    path("api/integrations/credentials/disconnect", views.integrations_credential_disconnect, name="integrations_credential_disconnect"),

    #  - Merge.dev Agent Handler: env-resident components (Hermes broker / MCP aggregator) reach Merge through these. Tenant-wide Merge API key lives only on HUMR.
    path("api/integrations/merge/ensure-registered-user", views.integrations_merge_ensure_registered_user, name="integrations_merge_ensure_registered_user"),
    path("api/integrations/merge/link-token", views.integrations_merge_link_token, name="integrations_merge_link_token"),
    path("api/integrations/merge/connectors", views.integrations_merge_connectors, name="integrations_merge_connectors"),
    path("api/integrations/merge/connector-status", views.integrations_merge_connector_status, name="integrations_merge_connector_status"),
    path("api/integrations/merge/disconnect", views.integrations_merge_disconnect, name="integrations_merge_disconnect"),
    path("api/integrations/merge/mcp", views.integrations_merge_mcp, name="integrations_merge_mcp"),

    # API endpoints (view implementations live under views/integrations/ or views/pdp.py)
    path("api/aws/install-account-callback", views.aws_install_account_callback, name="aws_install_account_callback"),
    #  - github_webhook: receives push/installation events from GitHub
    path("api/github/webhook", views.github_webhook, name="github_webhook"),
    #  - stripe_webhook: receives signed subscription and invoice lifecycle events from Stripe
    path("api/stripe/webhook", views.stripe_webhook, name="stripe_webhook"),
    #  - pdp_evaluate: called by policy proxies to authorize each request against ABAC
    path("api/pdp/evaluate", views.pdp_evaluate, name="pdp_evaluate"),
    path("api/pdp/evaluate-public", views.pdp_evaluate_public, name="pdp_evaluate_public"),
    #  - policy_proxy_activity: debounced last-authorized-traffic signal from each policy proxy
    path("api/runtime/policy-proxy-activity", views.policy_proxy_activity, name="policy_proxy_activity"),
    #  - billing_usage_events: batched LLM usage facts from each integrations broker
    path("api/runtime/billing-usage-events", views.billing_usage_events, name="billing_usage_events"),
    #  - billing_entitlement: pull the org's credit snapshot. Brokers normally refresh that
    #    cache from every usage-event response; this covers the case where nothing is spending.
    path("api/runtime/billing-entitlement", views.billing_entitlement, name="billing_entitlement"),

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

    # Staff-only platform fleet dashboard (cross-org by design; gated by is_staff)
    path("platform/fleet/", views.platform_fleet, name="platform_fleet"),
    path("platform/fleet/fail-unsettled/confirm/", views.fleet_fail_unsettled_deployments_confirm, name="fleet_fail_unsettled_deployments_confirm"),
    path("platform/fleet/fail-unsettled/", views.fleet_fail_unsettled_deployments, name="fleet_fail_unsettled_deployments"),
    path("platform/fleet/redeploy-all/confirm/", views.fleet_redeploy_all_confirm, name="fleet_redeploy_all_confirm"),
    path("platform/fleet/redeploy-all/", views.fleet_redeploy_all, name="fleet_redeploy_all"),
    path("platform/fleet/app/<uuid:app_id>/redeploy/", views.fleet_deployment_redeploy, name="fleet_deployment_redeploy"),
    path("platform/fleet/app/<uuid:app_id>/remove/confirm/", views.fleet_app_remove_confirm, name="fleet_app_remove_confirm"),
    path("platform/fleet/app/<uuid:app_id>/remove/", views.fleet_app_remove, name="fleet_app_remove"),
    path("platform/fleet/app/<uuid:app_id>/log/", views.fleet_deployment_log, name="fleet_deployment_log"),
    # Staff-only platform billing dashboard (cross-org by design; gated by is_staff)
    path("platform/billing/", views.platform_billing, name="platform_billing"),
    path("platform/billing/organization/<uuid:organization_id>/ledger/", views.platform_billing_organization_ledger, name="platform_billing_organization_ledger"),
    path("platform/billing/organization/<uuid:organization_id>/app/<uuid:app_id>/events/", views.platform_billing_app_events, name="platform_billing_app_events"),
]
