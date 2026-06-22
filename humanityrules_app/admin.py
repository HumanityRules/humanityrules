from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.http import HttpRequest

from humanityrules_app.views.integrations import provider_openrouter
from humanityrules_app.models import (
    AWSAccount,
    App,
    AppPermissions,
    AppRemovalJob,
    AppTemplate,
    Conversation,
    Datastore,
    Deployment,
    DeploymentBlueprint,
    DeploymentLog,
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
    LLMUsageLog,
    Message,
    Organization,
    OrganizationMembership,
    AwsResourceCache,
    AppPermissionRequest,
    Policy,
    EnvironmentBearerToken,
    Repository,
    ResourceTag,
    User,
    WaitlistSignup,
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
    list_display = ["name", "slug", "auth_provider", "default_org_role", "bootstrap_admin_email", "created_at", "updated_at"]
    list_filter = ["auth_provider"]
    search_fields = ["name", "slug", "bootstrap_admin_email", "oidc_issuer_url", "oidc_client_id"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]

    fieldsets = (
        (None, {
            "fields": ("name", "slug", "default_org_role", "bootstrap_admin_email"),
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
    list_display = ["name", "slug", "organization", "workspace", "repository", "source_template", "app_type", "build_strategy", "branch", "container_port", "status", "updated_at"]
    list_filter = ["status", "app_type", "build_strategy", "organization", "source_template"]
    search_fields = ["name", "slug", "workspace__name", "organization__name", "repository__full_name", "branch"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization", "workspace", "repository", "source_template", "created_by"]


@admin.register(DeploymentBlueprint)
class DeploymentBlueprintAdmin(admin.ModelAdmin):
    list_display = ["app", "environment", "status", "branch", "cpu", "memory", "compute_mode", "subdomain", "updated_at"]
    list_filter = ["status", "compute_mode"]
    search_fields = ["app__name", "app__slug", "environment__name", "branch"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["app", "environment", "datastore", "created_by"]


@admin.register(Datastore)
class DatastoreAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "workspace", "engine", "deployment_mode", "status", "updated_at"]
    list_filter = ["engine", "deployment_mode", "status", "workspace__organization"]
    search_fields = ["name", "slug", "workspace__name", "workspace__organization__name", "database_name"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["workspace", "created_by"]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = [
        "title", "mode", "status", "user", "organization", "context_workspace",
        "context_repository", "context_aws_account", "context_environment",
        "context_app", "context_deployment_blueprint", "updated_at",
    ]
    list_filter = ["mode", "status", "organization", "context_workspace"]
    search_fields = [
        "title", "user__email", "user__username", "organization__name", "context_workspace__name",
        "context_repository__full_name", "context_aws_account__name", "context_environment__name",
        "context_app__name", "session_id",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = [
        "user", "organization", "context_workspace", "context_repository", "context_aws_account",
        "context_environment", "context_app", "context_deployment_blueprint",
        "context_app_permission_request",
    ]
    filter_horizontal = ["deployments"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["created_at", "conversation", "role", "content_type", "short_content"]
    list_filter = ["role", "content_type"]
    search_fields = ["conversation__title", "conversation__user__email", "content"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["conversation"]

    @admin.display(description="Content")
    def short_content(self, obj: Message) -> str:
        if not obj.content:
            return ""
        return obj.content[:120]


@admin.register(Deployment)
class DeploymentAdmin(admin.ModelAdmin):
    list_display = ["app", "environment", "blueprint", "git_ref", "status", "created_at", "completed_at", "service_url"]
    list_filter = ["status", "environment", "app__workspace__organization"]
    search_fields = [
        "app__name",
        "app__workspace__name",
        "app__workspace__organization__name",
        "environment__name",
        "git_ref",
        "git_commit_sha",
        "image_uri",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["app", "environment", "blueprint", "created_by"]


@admin.register(DeploymentLog)
class DeploymentLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "deployment", "source", "level", "short_message"]
    list_filter = ["source", "level"]
    search_fields = ["deployment__app__name", "message"]
    readonly_fields = ["id", "created_at"]
    autocomplete_fields = ["deployment"]

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


@admin.register(LLMUsageLog)
class LLMUsageLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "user", "conversation", "source", "model_alias", "input_tokens", "output_tokens", "cost_usd", "duration_ms"]
    list_filter = ["source", "model_alias", "organization"]
    search_fields = ["organization__name", "user__email", "conversation__title", "model_alias"]
    readonly_fields = [
        "id", "organization", "user", "conversation", "source",
        "model_alias", "model_id", "input_tokens", "output_tokens",
        "cost_usd", "duration_ms", "num_turns", "created_at",
    ]
    autocomplete_fields = []

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: LLMUsageLog | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: LLMUsageLog | None = None) -> bool:
        return False


@admin.register(AppPermissions)
class AppPermissionsAdmin(admin.ModelAdmin):
    list_display = ["id", "app", "environment", "created_at", "updated_at"]
    list_filter = ["app__organization"]
    search_fields = ["app__name", "environment__name"]
    readonly_fields = ["id", "statements", "created_at", "updated_at"]
    autocomplete_fields = ["app", "environment"]


@admin.register(AppPermissionRequest)
class AppPermissionRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "app", "environment", "status", "created_by", "created_at", "updated_at"]
    list_filter = ["status", "app__organization"]
    search_fields = ["app__name", "environment__name", "created_by__email", "description", "status_message"]
    readonly_fields = ["id", "statements", "created_at", "updated_at"]
    autocomplete_fields = ["app", "environment", "created_by"]

    fieldsets = (
        (None, {
            "fields": ("app", "environment", "status", "status_message"),
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
# Async Jobs
# =============================================================================


@admin.register(AppRemovalJob)
class AppRemovalJobAdmin(admin.ModelAdmin):
    list_display = [
        "app_slug_snapshot", "app_name_snapshot", "workspace_slug_snapshot",
        "organization", "status", "teardown_first", "delete_secrets",
        "delete_persistent_data", "delete_policies", "created_at", "updated_at",
    ]
    list_filter = ["status", "teardown_first", "delete_secrets", "delete_persistent_data", "delete_policies", "organization"]
    search_fields = ["app_slug_snapshot", "app_name_snapshot", "workspace_slug_snapshot", "organization__name", "status_message"]
    readonly_fields = [
        "id", "organization", "app_id_snapshot", "app_slug_snapshot",
        "app_name_snapshot", "workspace_slug_snapshot", "created_by",
        "created_at", "updated_at",
    ]
    autocomplete_fields = []


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
        if provider == IntegrationUserCredential.Provider.OPENROUTER and isinstance(credentials, dict):
            api_key = str(credentials.get("api_key", "") or "").strip()
            if api_key:
                metadata, error = provider_openrouter.validate_shared_key(api_key=api_key)
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
