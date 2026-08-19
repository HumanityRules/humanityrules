"""Render and process the public contact page outside the authenticated app shell."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from humanityrules_app import contact_forms


@require_http_methods(["GET", "POST"])
def contact(request: HttpRequest) -> HttpResponse:
    """Show the contact form and store valid messages using post-redirect-get."""
    if request.method == "POST":
        form = contact_forms.ContactSubmissionForm(data=request.POST)
        if form.is_valid():
            if not form.cleaned_data["website"]:
                form.save()
            return redirect(to=f"{reverse(viewname='contact')}?sent=1")
    else:
        form = contact_forms.ContactSubmissionForm()

    context = {
        "form": form,
        "submitted": request.method == "GET" and request.GET.get("sent") == "1",
    }
    return render(request, "humanityrules_app/landing/contact_page.html", context=context)
