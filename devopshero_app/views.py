from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.templatetags.static import static
import random
from datetime import datetime


def get_app_shell_context(current_page):
    """
    Returns the common sidebar context used across all pages.
    
    Args:
        current_page: The name of the current page to mark as active (e.g., 'dashboard', 'workspaces')
    """
    navigation_items = [
        {"name": "Dashboard", "url": "/", "icon": "dashboard", "is_active": current_page == "dashboard"},
        {"name": "Workspaces", "url": "/workspaces/", "icon": "workspaces", "is_active": current_page == "workspaces"},
        {"name": "Projects", "url": "/projects/", "icon": "projects", "is_active": current_page == "projects"},
        {"name": "Calendar", "url": "/calendar/", "icon": "calendar", "is_active": current_page == "calendar"},
        {"name": "Documents", "url": "/documents/", "icon": "documents", "is_active": current_page == "documents"},
        {"name": "Reports", "url": "/reports/", "icon": "reports", "is_active": current_page == "reports"},
    ]
    
    # Sample organizations - in a real app, this would come from the user's organizations
    user_organizations = [
        {"name": "Heroicons", "url": "/organizations/heroicons/", "initial": "H", "is_active": False},
        {"name": "Tailwind Labs", "url": "/organizations/tailwind-labs/", "initial": "T", "is_active": False},
        {"name": "Workcation", "url": "/organizations/workcation/", "initial": "W", "is_active": False},
    ]
    
    # User menu items
    profile_menu_items = [
        {"name": "Your profile", "url": "/profile/"},
        {"name": "Sign out", "url": "/auth/logout/"},
    ]
    
    # Current user info - in a real app, this would come from request.user
    current_user = {
        "name": "Tom Cook",
        "avatar_url": "https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?ixlib=rb-1.2.1&ixid=eyJhcHBfaWQiOjEyMDd9&auto=format&fit=facearea&facepad=2&w=256&h=256&q=80",
    }
    
    return {
        "navigation_items": navigation_items,
        "user_organizations": user_organizations,
        "profile_menu_items": profile_menu_items,
        "current_user": current_user,
        "settings_url": "/settings/",
        "search_url": "/search/",
        "site_logo_url": static('devopshero_app/devops-hero-logo.png'),
        "site_name": "DevOps Hero",
    }


@login_required
def dashboard(request):
    if request.htmx:
        return render(request, "devopshero_app/dashboard.html", context = {})

    # Return app shell - content will be loaded via HTMX
    context = get_app_shell_context(current_page="dashboard")
    context["content_url"] = "/"
    return render(request, "devopshero_app/app_shell.html", context = context)


@login_required
def workspaces(request):
    if request.htmx:
        return render(request, "devopshero_app/workspaces.html", context = {})
        
    # Return app shell - content will be loaded via HTMX
    context = get_app_shell_context(current_page="workspaces")
    context["content_url"] = "/workspaces/"
    return render(request, "devopshero_app/app_shell.html", context = context)


@login_required
def random_quote(request):
    """Returns a partial HTML snippet with a random quote - for HTMX demo"""
    quotes = [
        ("The only way to do great work is to love what you do.", "Your mama"),
        ("Infrastructure as code is the foundation of modern DevOps.", "Anonymous"),
        ("Automate everything you can, so you can focus on what matters.", "DevOps Wisdom"),
        ("Fail fast, learn faster.", "UI/UX Engineer"),
        ("There is no cloud, it's just someone else's computer.", "Unknown"),
    ]
    quote, author = random.choice(quotes)
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    return render(request, "devopshero_app/partials/quote.html", {
        "quote": quote,
        "author": author,
        "timestamp": timestamp,
    })
