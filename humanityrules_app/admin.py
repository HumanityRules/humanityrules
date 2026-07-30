from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.http import HttpRequest

from humanityrules_app.views.integrations import provider_registry
from humanityrules_app.models import (
    AWSAccount,
    App,
    AppPermissions,
    AppTemplate,
    BillingUsageEvent,
    DeploymentLog,
    DeploymentRecord,
    Environment,
    EnvironmentLog,
    Group,
    GroupAttribute,
    GroupMembership,
    IdentityAttribute,
    IntegrationConfig,
    IntegrationGitProvider,
    IntegrationSharedCredential,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    AwsResourceCache,
    AppPermissionRequest,
    PlatformSharedCredential,
    Policy,
    EnvironmentBearerToken,
    Repository,
    ResourceTag,
    User,
    WaitlistSignup,
    WebappPublicGrant,
    Workspace,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["email", "username", "workos_user_id", "oidc_sub", "current_organization", "is_staff", "is_active"]
    list_filter = ["is_staff", "is_active", "current_organization"]
    search_fields = ["email", "username", "workos_user_id", "oidc_sub"]
    ordering = ["email"]
    autocomplete_fields = ["current_organization"]

    fieldsets = BaseUserAdmin.fieldsets + (
        ("WorkOS", {"fields": ("workos_user_id",)}),
        ("OIDC", {"fields": ("oidc_sub",)}),
        ("Current Organization", {"fields": ("current_organization",)}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("WorkOS", {"fields": ("workos_user_id",)}),
        ("OIDC", {"fields": ("oidc_sub",)}),
        ("Current Organization", {"fields": ("current_organization",)}),
    )


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "auth_provider", "llm_preset", "default_org_role", "bootstrap_admin_email", "created_at", "updated_at"]
    list_filter = ["auth_provider", "llm_preset"]
    search_fields = ["name", "slug", "bootstrap_admin_email", "oidc_issuer_url", "oidc_client_id"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]

    fieldsets = (
        (None, {
            "fields": ("name", "slug", "default_org_role", "bootstrap_admin_email", "llm_preset", "platform_capabilities"),
        }),
        ("Authentication", {
            "fields": ("auth_provider", "oidc_issuer_url", "oidc_client_id", "oidc_client_secret"),
        }),
        ("Metadata", {
            "fields": ("id", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "organization", "role", "created_at"]
    list_filter = ["role", "organization"]
    search_fields = ["user__email", "user__username", "organization__name"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["user", "organization"]


@admin.register(AWSAccount)
class AWSAccountAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "aws_account_id", "status", "created_at"]
    list_filter = ["status", "organization"]
    search_fields = ["name", "aws_account_id", "organization__name"]
    readonly_fields = ["id", "external_id", "created_at", "updated_at"]
    autocomplete_fields = ["organization", "created_by"]
    
    fieldsets = (
        (None, {
            "fields": ("name", "organization", "status", "status_message")
        }),
        ("AWS Details", {
            "fields": ("aws_account_id", "role_arn", "external_id")
        }),
        ("Metadata", {
            "fields": ("created_by", "created_at", "updated_at", "id"),
            "classes": ("collapse",)
        }),
    )


@admin.register(Environment)
class EnvironmentAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "aws_account", "aws_region", "status", "vpc_id", "cluster_arn", "created_at"]
    list_filter = ["status", "aws_region", "aws_account__organization"]
    search_fields = ["name", "slug", "aws_account__name", "aws_account__aws_account_id", "vpc_id"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["aws_account"]

    fieldsets = (
        (None, {
            "fields": ("name", "slug", "aws_account", "aws_region", "status", "status_message")
        }),
        ("Stack Names", {
            "fields": ("vpc_stack_name", "cluster_stack_name"),
            "classes": ("collapse",)
        }),
        ("AWS Outputs", {
            "fields": ("vpc_id", "cluster_arn"),
            "classes": ("collapse",)
        }),
        ("Shared ALB", {
            "fields": ("shared_alb_hosted_zone",),
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at", "id"),
            "classes": ("collapse",)
        }),
    )


@admin.register(IntegrationGitProvider)
class IntegrationGitProviderAdmin(admin.ModelAdmin):
    list_display = ["organization", "provider", "status", "installation_id", "created_at"]
    list_filter = ["provider", "status", "organization"]
    search_fields = ["organization__name", "installation_id"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization"]


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ["full_name", "organization", "provider", "external_id", "default_branch", "created_at"]
    list_filter = ["provider", "organization"]
    search_fields = ["name", "full_name", "organization__name", "clone_url", "external_id"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization", "integration"]


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "organization", "created_at", "updated_at"]
    list_filter = ["organization"]
    search_fields = ["name", "slug", "organization__name"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization", "created_by"]


@admin.register(AppTemplate)
class AppTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "category", "default_compute_mode", "alb_target_container", "efs_config", "prefill_name", "is_active", "updated_at"]
    list_filter = ["is_active", "category", "default_compute_mode"]
    search_fields = ["name", "slug", "description", "prefill_name"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "organization", "workspace", "environment", "source_template", "cpu", "memory", "compute_mode", "container_port", "job_status", "live_state", "updated_at"]
    list_filter = ["job_status", "live_state", "compute_mode", "organization", "source_template"]
    search_fields = ["name", "slug", "workspace__name", "organization__name", "environment__name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization", "workspace", "environment", "source_template", "created_by"]

    def get_readonly_fields(self, request: HttpRequest, obj: App | None) -> list[str]:
        # environment is immutable: every historical deployment resolves its env via
        # deployment.app.environment, so editing it would silently rewire the app's whole
        # history. Settable on add, frozen on change.
        if obj is None:
            return self.readonly_fields
        return [*self.readonly_fields, "environment"]


@admin.register(DeploymentRecord)
class DeploymentRecordAdmin(admin.ModelAdmin):
    list_display = ["created_at", "app", "event_type", "attempt_id", "created_by"]
    list_filter = ["event_type", "app__environment", "app__workspace__organization"]
    search_fields = [
        "app__name",
        "app__workspace__name",
        "app__workspace__organization__name",
        "app__environment__name",
    ]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["app", "created_by"]


@admin.register(DeploymentLog)
class DeploymentLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "app", "attempt_id", "source", "level", "short_message"]
    list_filter = ["source", "level"]
    search_fields = ["app__name", "message"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["app"]

    @admin.display(description="Message")
    def short_message(self, obj: DeploymentLog) -> str:
        if not obj.message:
            return ""
        return obj.message[:120]


@admin.register(EnvironmentLog)
class EnvironmentLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "environment", "source", "level", "short_message"]
    list_filter = ["source", "level"]
    search_fields = ["environment__name", "message"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["environment"]

    @admin.display(description="Message")
    def short_message(self, obj: EnvironmentLog) -> str:
        if not obj.message:
            return ""
        return obj.message[:120]


@admin.register(AppPermissions)
class AppPermissionsAdmin(admin.ModelAdmin):
    list_display = ["id", "app", "created_at", "updated_at"]
    list_filter = ["app__organization"]
    search_fields = ["app__name"]
    readonly_fields = ["id", "statements", "created_at", "updated_at"]
    autocomplete_fields = ["app"]


@admin.register(AppPermissionRequest)
class AppPermissionRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "app", "status", "created_by", "created_at", "updated_at"]
    list_filter = ["status", "app__organization"]
    search_fields = ["app__name", "created_by__email", "description", "status_message"]
    readonly_fields = ["id", "statements", "created_at", "updated_at"]
    autocomplete_fields = ["app", "created_by"]

    fieldsets = (
        (None, {
            "fields": ("app", "status", "status_message"),
        }),
        ("Request", {
            "fields": ("description", "statements", "created_by"),
        }),
        ("Metadata", {
            "fields": ("id", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(AwsResourceCache)
class AwsResourceCacheAdmin(admin.ModelAdmin):
    list_display = ["environment", "service", "fetched_at"]
    list_filter = ["service", "environment__aws_account__organization"]
    search_fields = ["service", "environment__name"]
    readonly_fields = ["environment", "service", "resources", "fetched_at"]


@admin.register(WaitlistSignup)
class WaitlistSignupAdmin(admin.ModelAdmin):
    list_display = ["email", "source", "created_at"]
    list_filter = ["source", "created_at"]
    search_fields = ["email"]
    readonly_fields = ["id", "created_at"]


# =============================================================================
# ABAC Models
# =============================================================================


@admin.register(IdentityAttribute)
class IdentityAttributeAdmin(admin.ModelAdmin):
    list_display = ["user", "organization", "key", "value", "created_at"]
    list_filter = ["organization", "key"]
    search_fields = ["user__email", "key", "value"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["organization", "user"]


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "created_at", "updated_at"]
    list_filter = ["organization"]
    search_fields = ["name", "organization__name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization"]


@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "group", "created_at"]
    list_filter = ["group__organization"]
    search_fields = ["user__email", "group__name"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["group", "user"]


@admin.register(GroupAttribute)
class GroupAttributeAdmin(admin.ModelAdmin):
    list_display = ["group", "key", "value", "created_at"]
    list_filter = ["group__organization", "key"]
    search_fields = ["group__name", "key", "value"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["group"]


@admin.register(ResourceTag)
class ResourceTagAdmin(admin.ModelAdmin):
    list_display = ["resource_type", "key", "value", "organization", "workspace", "environment", "app", "created_at"]
    list_filter = ["resource_type", "organization", "key"]
    search_fields = ["key", "value", "workspace__name", "environment__name", "app__name"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["organization", "workspace", "environment", "app"]


@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "resource_type", "is_system", "created_at", "updated_at"]
    list_filter = ["resource_type", "is_system", "organization"]
    search_fields = ["name", "organization__name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization"]


# =============================================================================
# Billing
# =============================================================================


@admin.register(BillingUsageEvent)
class BillingUsageEventAdmin(admin.ModelAdmin):
    list_display = ["occurred_at", "organization", "app_slug", "owner_username", "source", "subkey", "rated_at"]
    list_filter = ["source", ("rated_at", admin.EmptyFieldListFilter), "organization"]
    search_fields = [
        "idempotency_key",
        "app_slug",
        "owner_username",
        "subkey",
        "organization__name",
        "organization__slug",
    ]
    readonly_fields = [
        "id",
        "organization",
        "app_id",
        "app_slug",
        "owner_username",
        "source",
        "subkey",
        "quantities",
        "occurred_at",
        "idempotency_key",
        "rated_at",
        "created_at",
    ]
    date_hierarchy = "occurred_at"
    ordering = ["-occurred_at"]
    list_select_related = ["organization"]

    fieldsets = (
        ("Attribution", {
            "fields": ("organization", "app_id", "app_slug", "owner_username"),
        }),
        ("Usage", {
            "fields": ("source", "subkey", "quantities", "occurred_at"),
        }),
        ("Rating", {
            "fields": ("rated_at",),
        }),
        ("Metadata", {
            "fields": ("id", "idempotency_key", "created_at"),
            "classes": ("collapse",),
        }),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: BillingUsageEvent | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: BillingUsageEvent | None = None) -> bool:
        return False


# =============================================================================
# Infrastructure / Integrations
# =============================================================================


@admin.register(EnvironmentBearerToken)
class EnvironmentBearerTokenAdmin(admin.ModelAdmin):
    list_display = ["environment", "token_hash", "created_at"]
    search_fields = ["environment__name", "environment__slug", "token_hash"]
    readonly_fields = ["id", "token_hash", "created_at"]
    autocomplete_fields = ["environment"]


@admin.register(IntegrationConfig)
class IntegrationConfigAdmin(admin.ModelAdmin):
    list_display = ["provider", "created_at", "updated_at"]
    list_filter = ["provider"]
    search_fields = ["provider"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(IntegrationUserCredential)
class IntegrationUserCredentialAdmin(admin.ModelAdmin):
    list_display = ["owner_user", "environment", "app_slug", "provider", "created_at", "updated_at", "last_refreshed_at"]
    list_filter = ["provider", "environment"]
    search_fields = ["owner_user__email", "owner_user__username", "environment__name", "environment__slug", "app_slug"]
    readonly_fields = ["id", "created_at", "updated_at", "last_refreshed_at"]
    autocomplete_fields = ["owner_user", "environment"]


class IntegrationSharedCredentialForm(forms.ModelForm):
    """Admin form that validates scope/target coherence and live-checks OpenRouter keys."""

    class Meta:
        model = IntegrationSharedCredential
        fields = "__all__"

    def clean(self) -> dict:
        cleaned = super().clean()
        scope = cleaned.get("scope")
        target_user = cleaned.get("target_user")
        target_workspace = cleaned.get("target_workspace")
        if scope == IntegrationSharedCredential.Scope.USER:
            if target_user is None:
                raise forms.ValidationError({"target_user": "Required when scope is 'user'."})
            if target_workspace is not None:
                raise forms.ValidationError({"target_workspace": "Only set when scope is 'workspace'."})
        elif scope == IntegrationSharedCredential.Scope.WORKSPACE:
            if target_workspace is None:
                raise forms.ValidationError({"target_workspace": "Required when scope is 'workspace'."})
            if target_user is not None:
                raise forms.ValidationError({"target_user": "Only set when scope is 'user'."})
        elif scope == IntegrationSharedCredential.Scope.EVERYONE and (target_user is not None or target_workspace is not None):
            raise forms.ValidationError("Scope 'everyone' must not set a target user or workspace.")

        provider = cleaned.get("provider")
        credentials = cleaned.get("credentials")
        spec = provider_registry.get(provider=provider) if provider else None
        validate_shared_key = getattr(spec.module, "validate_shared_key", None) if spec is not None else None
        if validate_shared_key is not None and isinstance(credentials, dict):
            # Require (and live-validate) a real key: a blank share resolves to
            # `absent` at refresh time and would shadow every user's personal key
            # org-wide. validate_shared_key rejects an empty key.
            api_key = str(credentials.get("api_key", "") or "").strip()
            metadata, error = validate_shared_key(api_key=api_key)
            if error is not None:
                raise forms.ValidationError({"credentials": error})
            cleaned["metadata"] = metadata
        return cleaned


@admin.register(IntegrationSharedCredential)
class IntegrationSharedCredentialAdmin(admin.ModelAdmin):
    form = IntegrationSharedCredentialForm
    list_display = ["organization", "provider", "scope", "target_user", "target_workspace", "created_by", "created_at", "updated_at"]
    list_filter = ["provider", "scope", "organization"]
    search_fields = ["organization__name", "organization__slug", "target_user__email", "target_workspace__name"]
    readonly_fields = ["id", "created_at", "updated_at", "metadata"]
    autocomplete_fields = ["organization", "target_user", "target_workspace", "created_by"]

    def save_model(self, request: HttpRequest, obj: IntegrationSharedCredential, form: forms.ModelForm, change: bool) -> None:
        if obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class PlatformSharedCredentialForm(forms.ModelForm):
    """Admin form that live-validates a platform credential's key via the provider."""

    class Meta:
        model = PlatformSharedCredential
        fields = "__all__"

    def clean(self) -> dict:
        cleaned = super().clean()
        provider = cleaned.get("provider")
        credentials = cleaned.get("credentials")
        spec = provider_registry.get(provider=provider) if provider else None
        validate_shared_key = getattr(spec.module, "validate_shared_key", None) if spec is not None else None
        if validate_shared_key is not None and isinstance(credentials, dict):
            # Require (and live-validate) a real key. A blank platform share would
            # resolve to `absent` at refresh time and just fall through to the
            # personal/org path; validate_shared_key rejects an empty key.
            api_key = str(credentials.get("api_key", "") or "").strip()
            metadata, error = validate_shared_key(api_key=api_key)
            if error is not None:
                raise forms.ValidationError({"credentials": error})
            cleaned["metadata"] = metadata
        return cleaned


@admin.register(PlatformSharedCredential)
class PlatformSharedCredentialAdmin(admin.ModelAdmin):
    form = PlatformSharedCredentialForm
    list_display = ["provider", "enabled", "created_by", "created_at", "updated_at"]
    list_filter = ["provider", "enabled"]
    readonly_fields = ["id", "created_at", "updated_at", "metadata"]
    autocomplete_fields = ["created_by"]

    # Platform-wide credentials apply to every customer org, so they are superuser-only
    # (the spec; the org-admin Provider Keys UI gates the "All customers" scope separately).
    def has_view_permission(self, request: HttpRequest, obj: PlatformSharedCredential | None = None) -> bool:
        return request.user.is_superuser

    def has_add_permission(self, request: HttpRequest) -> bool:
        return request.user.is_superuser

    def has_change_permission(self, request: HttpRequest, obj: PlatformSharedCredential | None = None) -> bool:
        return request.user.is_superuser

    def has_delete_permission(self, request: HttpRequest, obj: PlatformSharedCredential | None = None) -> bool:
        return request.user.is_superuser

    def save_model(self, request: HttpRequest, obj: PlatformSharedCredential, form: forms.ModelForm, change: bool) -> None:
        if obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(WebappPublicGrant)
class WebappPublicGrantAdmin(admin.ModelAdmin):
    list_display = ["slug", "app", "granted_by", "created_at", "expires_at", "revoked_at", "revoked_by"]
    list_filter = ["app__environment"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["app", "granted_by", "revoked_by"]
