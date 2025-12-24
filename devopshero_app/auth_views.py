from django.conf import settings
from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.http import HttpResponseBadRequest
from workos import WorkOSClient

from .models import User


# Initialize WorkOS client
workos_client = WorkOSClient(
    api_key=settings.WORKOS_API_KEY,
    client_id=settings.WORKOS_CLIENT_ID,
)


def auth_login(request):
    """
    Initiates the WorkOS OAuth flow by redirecting to the authorization URL.
    """
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    
    # Check if this is a direct navigation vs showing login page
    if request.method == "GET" and not request.GET.get("start"):
        return render(request, "devopshero_app/login.html")
    
    # Generate authorization URL and redirect
    authorization_url = workos_client.user_management.get_authorization_url(
        provider="authkit",
        redirect_uri=settings.WORKOS_REDIRECT_URI,
    )
    
    return redirect(authorization_url)


def auth_callback(request):
    """
    Handles the OAuth callback from WorkOS.
    Authenticates the user with the provided authorization code.
    """
    code = request.GET.get("code")
    
    if not code:
        return HttpResponseBadRequest("Missing authorization code")
    
    try:
        # Exchange code for user info
        auth_response = workos_client.user_management.authenticate_with_code(
            code=code,
        )
        
        workos_user = auth_response.user
        
        # Get or create the Django user
        user, created = User.objects.get_or_create(
            workos_user_id=workos_user.id,
            defaults={
                "email": workos_user.email,
                "username": workos_user.email,
                "first_name": workos_user.first_name or "",
                "last_name": workos_user.last_name or "",
            }
        )
        
        # Update user info if not newly created (in case profile changed in WorkOS)
        if not created:
            user.email = workos_user.email
            user.first_name = workos_user.first_name or ""
            user.last_name = workos_user.last_name or ""
            user.save()
        
        # Log the user in
        login(request, user)
        
        return redirect(settings.LOGIN_REDIRECT_URL)
        
    except Exception as e:
        # Log the error in production
        return HttpResponseBadRequest(f"Authentication failed: {str(e)}")


def auth_logout(request):
    """
    Logs the user out of Django session.
    """
    logout(request)
    return redirect(settings.LOGIN_URL)

