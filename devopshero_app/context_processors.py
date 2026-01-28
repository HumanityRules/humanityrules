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

    config = {
        'apiKey': api_key,
        'apiHost': getattr(settings, 'POSTHOG_HOST', 'https://us.i.posthog.com'),
        'user': user_data,
    }
    return {'posthog_config_json': json.dumps(config)}
