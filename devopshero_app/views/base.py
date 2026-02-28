from django.templatetags.static import static

from ..models import OrganizationMembership
from ..services import abac


def get_app_shell_context(request, current_page):
    """
    Returns the common sidebar context used across all pages.
    
    Args:
        request: The HTTP request object (needed for user and session)
        current_page: The name of the current page to mark as active (e.g., 'dashboard', 'workspaces')
    """
    user = request.user
    current_org = user.current_organization
    
    # Get user's organizations via memberships
    memberships = OrganizationMembership.objects.filter(user=user).select_related('organization')
    user_organizations = [
        {"id": str(m.organization.id), "name": m.organization.name}
        for m in memberships
    ]
    
    # Current organization as dict for template
    current_organization = {"id": str(current_org.id), "name": current_org.name}
    
    navigation_items = [
        {"name": "Dashboard", "url": "/dashboard/", "icon": "dashboard", "is_active": current_page == "dashboard"},
        {"name": "Chat", "url": "/chat/", "icon": "chat", "is_active": current_page == "chat"},
        {"name": "Workspaces", "url": "/workspaces/", "icon": "workspaces", "is_active": current_page == "workspaces"},
        {"name": "Environments", "url": "/environments/", "icon": "environments", "is_active": current_page == "environments"},
        {"name": "Security", "url": "/security/hub/", "icon": "security", "is_active": current_page == "security"},
        {"name": "Settings", "url": "/settings/", "icon": "settings", "is_active": current_page == "settings"},
    ]
    
    profile_menu_items = [
        {"name": "Your profile", "url": "/settings/personal/"},
        {"name": "Sign out", "url": "/auth/logout/"},
    ]
    
    # Build user display name and initials
    if user.first_name or user.last_name:
        display_name = f"{user.first_name} {user.last_name}".strip()
        initials = f"{user.first_name[:1]}{user.last_name[:1]}".upper()
    else:
        display_name = user.email or user.username
        initials = display_name[:2].upper()
    
    current_user = {
        "name": display_name,
        "email": user.email,
        "initials": initials,
    }
    
    return {
        "navigation_items": navigation_items,
        "user_organizations": user_organizations,
        "current_organization": current_organization,
        "profile_menu_items": profile_menu_items,
        "current_user": current_user,
        "search_url": "/search/",
        "site_logo_url": static('devopshero_app/devops-hero-logo-large.png'),
        "site_name": "DevOps Hero",
        "user_is_org_admin": abac.is_org_admin(organization=current_org, user=user),
    }
