from django.shortcuts import render


def landing(request):
    """Render the public landing page."""
    context = {
        "is_authenticated": request.user.is_authenticated,
    }
    return render(request, "humanityrules_app/landing/landing_page.html", context=context)

