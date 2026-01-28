"""Django context processors for template variables."""
import json

from django.conf import settings


def posthog_context(request):
    """Inject PostHog settings into all templates."""
    api_key = getattr(settings, 'POSTHOG_API_KEY', None)
    if not api_key:
        return {'posthog_config_json': None}

    user = request.user
    user_data = None
    if user.is_authenticated:
        user_data = {
            'id': str(user.id),
            'email': user.email,
            'name': user.get_full_name() or user.email,
        }

    # Use reverse proxy if configured (bypasses ad blockers)
    # Proxy paths: /doh-ph/* for API, /doh-ph-static/* for static assets
    proxy_host = getattr(settings, 'POSTHOG_PROXY_HOST', None)
    api_host = proxy_host if proxy_host else getattr(settings, 'POSTHOG_HOST', 'https://us.i.posthog.com')

    config = {
        'apiKey': api_key,
        'apiHost': api_host,
        'useProxy': bool(proxy_host),
        'user': user_data,
    }
    return {'posthog_config_json': json.dumps(config)}
