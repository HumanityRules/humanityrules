from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from devopshero_app.models import Organization, OrganizationMembership, User


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
