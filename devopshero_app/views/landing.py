from django.shortcuts import render
from django.templatetags.static import static


def landing(request):
    context = {
        "is_authenticated": request.user.is_authenticated,
        "site_logo_url": static('devopshero_app/devops-hero-logo-large.png'),
    }
    return render(request, "devopshero_app/landing.html", context=context)

