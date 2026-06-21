import random
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from ..models import Organization, OrganizationMembership


@login_required
def switch_organization(request):
    """Switch the current organization and reload the page."""
    org_id = request.POST.get("organization")
    user = request.user
    current_org_id = str(user.current_organization_id)
    
    # Only switch if different from current (prevents double-submit issues)
    if org_id != current_org_id:
        # Verify user has access to this organization
        if OrganizationMembership.objects.filter(user=user, organization_id=org_id).exists():
            user.current_organization = Organization.objects.get(id=org_id)
            user.save(update_fields=["current_organization"])
            response = HttpResponse()
            response["HX-Redirect"] = "/dashboard/"
            return response
    
    # No change needed
    return HttpResponse()


@login_required
def random_quote(request):
    """Returns a partial HTML snippet with a random quote - for HTMX demo"""
    quotes = [
        ("The only way to do great work is to love what you do.", "Your mama"),
        ("Infrastructure as code is the foundation of modern DevOps.", "Anonymous"),
        ("Automate everything you can, so you can focus on what matters.", "DevOps Wisdom"),
        ("Fail fast, learn faster.", "UI/UX Engineer"),
        ("There is no cloud, it's just someone else's computer.", "Unknown"),
    ]
    quote, author = random.choice(quotes)
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    return render(request, "humanityrules_app/partials/quote.html", {
        "quote": quote,
        "author": author,
        "timestamp": timestamp,
    })

