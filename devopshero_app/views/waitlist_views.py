from django.db import IntegrityError
from django.http import HttpResponse
from django.views.decorators.http import require_POST

from devopshero_app import models


@require_POST
def waitlist_signup(request):
    """Handle waitlist signup form submission via HTMX."""
    email = request.POST.get("email", "").strip()
    source = request.POST.get("source", "")

    if not email:
        return HttpResponse(
            '<p class="text-sm text-red-400">Please enter a valid email.</p>',
            status=400,
        )

    try:
        models.WaitlistSignup.objects.create(email=email, source=source)
    except IntegrityError:
        # Email already exists - still show success (don't leak info)
        pass

    return HttpResponse(
        '<p class="text-sm text-cyber-400">Thanks! We\'ll notify you when DevOps Hero is ready.</p>'
    )
