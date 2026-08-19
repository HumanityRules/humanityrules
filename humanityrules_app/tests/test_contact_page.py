"""Tests for the public contact page and its landing-page entry point."""

from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from humanityrules_app import models


class ContactPageTests(TestCase):
    def test_landing_footer_links_to_contact_page(self) -> None:
        response = self.client.get(path=reverse(viewname="landing"))

        self.assertContains(response=response, text='href="/contact/"')

    def test_contact_page_is_public(self) -> None:
        response = self.client.get(path=reverse(viewname="contact"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response=response, template_name="humanityrules_app/landing/contact_page.html")
        self.assertContains(response=response, text="Send us a message")
        self.assertContains(response=response, text='name="name"')
        self.assertContains(response=response, text='name="email"')
        self.assertContains(response=response, text='name="company"')
        self.assertContains(response=response, text='name="message"')

    def test_valid_submission_is_stored_then_redirected_to_success_state(self) -> None:
        response = self.client.post(
            path=reverse(viewname="contact"),
            data={
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "company": "Analytical Engines",
                "message": "We want to govern our internal agents.",
            },
        )

        self.assertRedirects(response=response, expected_url="/contact/?sent=1", fetch_redirect_response=False)
        submission = models.ContactSubmission.objects.get()
        self.assertEqual(submission.name, "Ada Lovelace")
        self.assertEqual(submission.email, "ada@example.com")
        self.assertEqual(submission.company, "Analytical Engines")
        self.assertEqual(submission.message, "We want to govern our internal agents.")

        success_response = self.client.get(path="/contact/?sent=1")
        self.assertContains(response=success_response, text="Message received")
        self.assertNotContains(response=success_response, text='name="message"')

    def test_company_is_optional(self) -> None:
        response = self.client.post(
            path=reverse(viewname="contact"),
            data={
                "name": "Grace Hopper",
                "email": "grace@example.com",
                "company": "",
                "message": "Please tell me more.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.ContactSubmission.objects.get().company, "")

    def test_invalid_submission_preserves_values_and_shows_errors(self) -> None:
        response = self.client.post(
            path=reverse(viewname="contact"),
            data={
                "name": "Lin",
                "email": "not-an-email",
                "company": "Example Co",
                "message": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response=response, text="Enter a valid email address.")
        self.assertContains(response=response, text="This field is required.")
        self.assertContains(response=response, text="Example Co")
        self.assertFalse(models.ContactSubmission.objects.exists())

    def test_honeypot_submission_looks_successful_but_is_not_stored(self) -> None:
        response = self.client.post(
            path=reverse(viewname="contact"),
            data={
                "name": "Automated Sender",
                "email": "bot@example.com",
                "company": "",
                "message": "Unwanted message",
                "website": "https://spam.example.com",
            },
        )

        self.assertRedirects(response=response, expected_url="/contact/?sent=1", fetch_redirect_response=False)
        self.assertFalse(models.ContactSubmission.objects.exists())

    def test_message_length_is_limited(self) -> None:
        response = self.client.post(
            path=reverse(viewname="contact"),
            data={
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "company": "",
                "message": "x" * 5001,
            },
        )

        self.assertContains(response=response, text="Ensure this value has at most 5000 characters")
        self.assertFalse(models.ContactSubmission.objects.exists())

    def test_contact_submission_is_registered_in_admin(self) -> None:
        self.assertIn(models.ContactSubmission, admin.site._registry)
