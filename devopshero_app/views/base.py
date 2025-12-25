from django.templatetags.static import static


def get_app_shell_context(current_page):
    """
    Returns the common sidebar context used across all pages.
    
    Args:
        current_page: The name of the current page to mark as active (e.g., 'dashboard', 'workspaces')
    """
    navigation_items = [
        {"name": "Dashboard", "url": "/dashboard/", "icon": "dashboard", "is_active": current_page == "dashboard"},
        {"name": "Workspaces", "url": "/workspaces/", "icon": "workspaces", "is_active": current_page == "workspaces"},
        {"name": "Apps", "url": "/apps/", "icon": "apps", "is_active": current_page == "apps"},
        {"name": "Datastores", "url": "/datastores/", "icon": "datastores", "is_active": current_page == "datastores"},
        {"name": "Security", "url": "/security/", "icon": "security", "is_active": current_page == "security"},
        {"name": "Settings", "url": "/settings/", "icon": "settings", "is_active": current_page == "settings"},
    ]
    
    user_organizations = [
        {"name": "Heroicons", "url": "/organizations/heroicons/", "initial": "H", "is_active": False},
        {"name": "Tailwind Labs", "url": "/organizations/tailwind-labs/", "initial": "T", "is_active": False},
        {"name": "Workcation", "url": "/organizations/workcation/", "initial": "W", "is_active": False},
    ]
    
    fake_organizations = [
        {"id": "1", "name": "Acme Corporation"},
        {"id": "2", "name": "Stark Industries"},
        {"id": "3", "name": "Wayne Enterprises"},
    ]
    
    profile_menu_items = [
        {"name": "Your profile", "url": "/profile/"},
        {"name": "Sign out", "url": "/auth/logout/"},
    ]
    
    current_user = {
        "name": "Tom Cook",
        "avatar_url": "https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?ixlib=rb-1.2.1&ixid=eyJhcHBfaWQiOjEyMDd9&auto=format&fit=facearea&facepad=2&w=256&h=256&q=80",
    }
    
    return {
        "navigation_items": navigation_items,
        "user_organizations": user_organizations,
        "fake_organizations": fake_organizations,
        "profile_menu_items": profile_menu_items,
        "current_user": current_user,
        "search_url": "/search/",
        "site_logo_url": static('devopshero_app/devops-hero-logo-large.png'),
        "site_name": "DevOps Hero",
    }

