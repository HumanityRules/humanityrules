from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from humanityrules_app.services.billing import plans


def landing(request: HttpRequest) -> HttpResponse:
    """Render the public landing page."""
    trial = plans.PLANS[plans.TRIAL]
    context = {
        "is_authenticated": request.user.is_authenticated,
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
