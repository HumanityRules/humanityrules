from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.http import HttpRequest

from devopshero_app.models import (
    AWSAccount,
    App,
    AppPermissions,
    AppTemplate,
    Conversation,
    Datastore,
    Deployment,
    DeploymentBlueprint,
    DeploymentLog,
    Environment,
    EnvironmentLog,
    GitProviderIntegration,
    Group,
    GroupAttribute,
    GroupMembership,
    IdentityAttribute,
    LLMUsageLog,
    Message,
    Organization,
    OrganizationMembership,
    AwsResourceCache,
    AppPermissionRequest,
    Policy,
    Repository,
    ResourceTag,
    User,
    WaitlistSignup,
    Workspace,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["email", "username", "workos_user_id", "oidc_sub", "is_staff", "is_active"]
    list_filter = ["is_staff", "is_active"]
    search_fields = ["email", "username", "workos_user_id", "oidc_sub"]
    ordering = ["email"]

    fieldsets = BaseUserAdmin.fieldsets + (
        ("WorkOS", {"fields": ("workos_user_id",)}),
        ("OIDC", {"fields": ("oidc_sub",)}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("WorkOS", {"fields": ("workos_user_id",)}),
        ("OIDC", {"fields": ("oidc_sub",)}),
    )


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "created_at", "updated_at"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]


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
        ("Metadata", {
            "fields": ("created_at", "updated_at", "id"),
            "classes": ("collapse",)
        }),
    )


@admin.register(GitProviderIntegration)
class GitProviderIntegrationAdmin(admin.ModelAdmin):
    list_display = ["organization", "provider", "status", "installation_id", "created_at"]
    list_filter = ["provider", "status", "organization"]
    search_fields = ["organization__name", "installation_id"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization"]


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ["full_name", "organization", "provider", "default_branch", "created_at"]
    list_filter = ["provider", "organization"]
    search_fields = ["name", "full_name", "organization__name", "clone_url"]
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
    list_display = ["name", "slug", "category", "app_type", "efs_config", "is_active", "updated_at"]
    list_filter = ["is_active", "category", "app_type"]
    search_fields = ["name", "slug", "description"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "organization", "workspace", "repository", "app_type", "build_strategy", "branch", "container_port", "updated_at"]
    list_filter = ["app_type", "build_strategy", "organization"]
    search_fields = ["name", "slug", "workspace__name", "organization__name", "repository__full_name", "branch"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization", "workspace", "repository", "created_by"]


@admin.register(DeploymentBlueprint)
class DeploymentBlueprintAdmin(admin.ModelAdmin):
    list_display = ["app", "environment", "status", "cpu", "memory", "subdomain", "updated_at"]
    list_filter = ["status"]
    search_fields = ["app__name", "app__slug", "environment__name"]
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
        "context_repository", "context_aws_account", "context_environment", "updated_at",
    ]
    list_filter = ["mode", "status", "organization", "context_workspace"]
    search_fields = [
        "title", "user__email", "user__username", "organization__name", "context_workspace__name",
        "context_repository__full_name", "context_aws_account__name", "context_environment__name", "session_id",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = [
        "user", "organization", "context_workspace", "context_repository", "context_aws_account",
        "context_environment", "context_app_permission_request",
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
    list_display = ["app", "environment", "git_ref", "status", "created_at", "completed_at", "service_url"]
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
    autocomplete_fields = ["app", "environment", "created_by"]


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
    search_fields = ["app__name", "environment__name", "created_by__email", "status_message"]
    readonly_fields = ["id", "statements", "created_at", "updated_at"]
    autocomplete_fields = ["app", "environment", "created_by"]


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
