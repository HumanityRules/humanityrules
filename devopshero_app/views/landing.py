from django.shortcuts import render


def landing(request):
    """Render the public landing page."""
    return render(request, "devopshero_app/landing/landing_page.html")

