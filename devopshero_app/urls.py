from django.urls import path
from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("workspaces/", views.workspaces, name="workspaces"),
    path("apps/", views.apps, name="apps"),
    path("datastores/", views.datastores, name="datastores"),
    path("security/", views.security, name="security"),
    path("settings/", views.settings, name="settings"),
    path("settings/organization/", views.settings_organization, name="settings_organization"),
    path("settings/members/", views.settings_members, name="settings_members"),
    path("settings/aws-accounts/", views.settings_aws_accounts, name="settings_aws_accounts"),
    path("settings/aws-accounts/add/", views.settings_aws_accounts_add, name="settings_aws_accounts_add"),
    path("settings/billing/", views.settings_billing, name="settings_billing"),
    path("random-quote/", views.random_quote, name="random_quote"),
    
    # Authentication
    path("auth/login/", views.auth_login, name="login"),
    path("auth/callback/", views.auth_callback, name="auth_callback"),
    path("auth/logout/", views.auth_logout, name="logout"),
    
    # Onboarding
    path("onboarding/", views.onboarding, name="onboarding"),
]