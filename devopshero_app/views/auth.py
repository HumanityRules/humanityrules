from django.conf import settings
from django.contrib.auth import login, logout
from django.shortcuts import redirect
from django.http import HttpResponseBadRequest
from workos import WorkOSClient

from ..models import User


# Initialize WorkOS client
workos_client = WorkOSClient(
    api_key=settings.WORKOS_API_KEY,
    client_id=settings.WORKOS_CLIENT_ID,
)


def auth_login(request):
    """
    Redirects directly to WorkOS AuthKit for authentication.
    """
    if request.user.is_authenticated:
        return redirect("/dashboard/")
    
    # Build redirect URI dynamically from the current request
    scheme = 'https' if request.is_secure() or 'ngrok' in request.get_host() else 'http'
    redirect_uri = f"{scheme}://{request.get_host()}/auth/callback"

    print(f"Redirect URI: {redirect_uri}")
    
    authorization_url = workos_client.user_management.get_authorization_url(
        provider="authkit",
        redirect_uri=redirect_uri,
    )
    
    return redirect(authorization_url)


def auth_callback(request):
    """
    Handles the OAuth callback from WorkOS.
    For existing users: logs them in and redirects to dashboard.
    For new users: stores WorkOS info in session and redirects to onboarding.
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
        
        # Check if user already exists
        try:
            user = User.objects.get(workos_user_id=workos_user.id)
            # Existing user - update info and log in
            user.email = workos_user.email
            user.first_name = workos_user.first_name or ""
            user.last_name = workos_user.last_name or ""
            user.save()
            login(request, user)
            return redirect("/dashboard/")
        
        except User.DoesNotExist:
            # New user - store WorkOS info in session for onboarding
            request.session["pending_workos_user"] = {
                "workos_user_id": workos_user.id,
                "email": workos_user.email,
                "first_name": workos_user.first_name or "",
                "last_name": workos_user.last_name or "",
            }
            return redirect("/onboarding/")
        
    except Exception as e:
        # Log the error in production
        return HttpResponseBadRequest(f"Authentication failed: {str(e)}")


def auth_logout(request):
    """
    Logs the user out of Django session.
    """
    logout(request)
    return redirect("/")

