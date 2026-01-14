"""Views for the notes application."""
from django.http import JsonResponse


def health_check(request):
    """Health check endpoint for load balancers and monitoring."""
    return JsonResponse({'status': 'healthy'})


def index(request):
    """Landing page showing app info."""
    return JsonResponse({
        'app': 'django_postgres_app',
        'version': '1.0.0',
        'endpoints': {
            'health': '/health',
            'admin': '/admin/',
        },
    })
