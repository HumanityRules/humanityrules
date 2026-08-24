"""Render the public legal and company pages (privacy policy, terms of service, security, team) outside the authenticated app shell."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def privacy(request: HttpRequest) -> HttpResponse:
    """Render the public privacy policy page."""
    return render(request, "humanityrules_app/landing/privacy_page.html")


def terms(request: HttpRequest) -> HttpResponse:
    """Render the public terms of service page."""
    return render(request, "humanityrules_app/landing/terms_page.html")


def security(request: HttpRequest) -> HttpResponse:
    """Render the public security page."""
    return render(request, "humanityrules_app/landing/security_page.html")


def team(request: HttpRequest) -> HttpResponse:
    """Render the public team page."""
    return render(request, "humanityrules_app/landing/team_page.html")
