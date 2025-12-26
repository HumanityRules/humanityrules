from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from devopshero_app.models import AWSAccount, Organization, OrganizationMembership, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["email", "username", "workos_user_id", "is_staff", "is_active"]
    list_filter = ["is_staff", "is_active"]
    search_fields = ["email", "username", "workos_user_id"]
    ordering = ["email"]

    # Add workos_user_id to the fieldsets
    fieldsets = BaseUserAdmin.fieldsets + (
        ("WorkOS", {"fields": ("workos_user_id",)}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("WorkOS", {"fields": ("workos_user_id",)}),
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
