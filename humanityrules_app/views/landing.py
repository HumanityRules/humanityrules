from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from humanityrules_app import models
from humanityrules_app.services.billing import plans


def landing(request: HttpRequest) -> HttpResponse:
    """Render the public landing page."""
    trial = plans.PLANS[plans.TRIAL]
    platform_settings = models.PlatformSettings.objects.filter(pk=1).first()
    context = {
        "is_authenticated": request.user.is_authenticated,
        "public_signup_enabled": bool(platform_settings and platform_settings.public_signup_enabled),
        "trial_runtime_days": trial.trial_runtime_days,
        "operator_price_usd": plans.PLANS[plans.OPERATOR].price_usd_month,
        "team_agent_price_usd": plans.PLANS[plans.TEAM].price_usd_agent_month,
    }
    return render(request, "humanityrules_app/landing/landing_page.html", context=context)


def devopshero_landing(request: HttpRequest) -> HttpResponse:
    """Frozen copy of the original DevOpsHero landing page."""
    context = {
        "is_authenticated": request.user.is_authenticated,
    }
    return render(request, "humanityrules_app/landing/devopshero/landing_page.html", context=context)
