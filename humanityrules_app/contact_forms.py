"""Validate messages submitted through the public contact page before they are stored."""

from django import forms

from humanityrules_app import models


_FIELD_CLASSES = (
    "w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white "
    "placeholder:text-gray-600 transition-colors focus:border-cyber-400 focus:outline-none "
    "focus:ring-2 focus:ring-cyber-400/20"
)


class ContactSubmissionForm(forms.ModelForm):
    """Collect the information needed to respond to a prospective customer."""

    website = forms.CharField(
        label="",
        required=False,
        widget=forms.HiddenInput(attrs={"autocomplete": "off", "tabindex": "-1"}),
    )

    class Meta:
        model = models.ContactSubmission
        fields = ["name", "email", "company", "message"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "autocomplete": "name",
                    "class": _FIELD_CLASSES,
                    "placeholder": "Your name",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "autocomplete": "email",
                    "class": _FIELD_CLASSES,
                    "placeholder": "you@company.com",
                }
            ),
            "company": forms.TextInput(
                attrs={
                    "autocomplete": "organization",
                    "class": _FIELD_CLASSES,
                    "placeholder": "Company name",
                }
            ),
            "message": forms.Textarea(
                attrs={
                    "class": _FIELD_CLASSES,
                    "placeholder": "Tell us what you are working on and how we can help.",
                    "rows": 6,
                }
            ),
        }
