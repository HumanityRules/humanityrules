"""The shared-credential admin form must reject a blank OpenRouter key.

A share saved without a usable secret resolves to `absent` at refresh time and
would shadow every user's own pasted key org-wide (see token_refresh_batch),
so the admin form rejects it at save time rather than letting it through.
"""

import json

from django.test import TestCase

from humanityrules_app.admin import IntegrationSharedCredentialForm
from humanityrules_app.models import Organization


class TestSharedCredentialAdminFormValidation(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Admin Form Org", slug="admin-form-org")

    def _openrouter_form(self, credentials: dict) -> IntegrationSharedCredentialForm:
        return IntegrationSharedCredentialForm(data={
            "organization": str(self.org.pk),
            "provider": "openrouter",
            "scope": "everyone",
            "credentials": json.dumps(credentials),
            "config": json.dumps({}),
        })

    def test_rejects_blank_api_key(self) -> None:
        form = self._openrouter_form(credentials={"api_key": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("credentials", form.errors)

    def test_rejects_empty_credentials(self) -> None:
        form = self._openrouter_form(credentials={})
        self.assertFalse(form.is_valid())
        self.assertIn("credentials", form.errors)
