from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health_check(request: HttpRequest) -> JsonResponse:
    """Health check endpoint for ALB/ECS health checks."""
    return JsonResponse({"status": "healthy"})
