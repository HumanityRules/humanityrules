from django.shortcuts import render


def landing(request):
    """Render the public landing page."""
    context = {
        "is_authenticated": request.user.is_authenticated,
    }
    return render(request, "humanityrules_app/landing/landing_page.html", context=context)


def devopshero_landing(request):
    """Frozen copy of the original DevOpsHero landing page."""
    context = {
        "is_authenticated": request.user.is_authenticated,
    }
    return render(request, "humanityrules_app/landing/devopshero/landing_page.html", context=context)

