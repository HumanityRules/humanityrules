from django.test import TestCase

from humanityrules_app import models


class LandingPublicSignupTests(TestCase):
    def setUp(self) -> None:
        self.platform_settings = models.PlatformSettings.objects.get(pk=1)

    def test_public_signup_disabled_shows_waitlist(self) -> None:
        response = self.client.get(path="/")

        self.assertContains(response=response, text="Get a trial slot")
        self.assertContains(
            response=response,
            text="The product is live. Trial capacity is limited, so leave your email and we’ll let you know when a slot opens.",
        )
        self.assertContains(response=response, text="Bring it to your own cloud")
        self.assertContains(response=response, text="Humanity Rules — The workspace that builds itself.")
        self.assertContains(response=response, text='id="waitlist"')
        self.assertContains(response=response, text='hx-post="/waitlist/signup/"')
        self.assertNotContains(response=response, text="/auth/login/?screen_hint=sign-up")

    def test_public_signup_enabled_shows_signup_links(self) -> None:
        self.platform_settings.public_signup_enabled = True
        self.platform_settings.save(update_fields=["public_signup_enabled"])

        response = self.client.get(path="/")

        self.assertContains(response=response, text="/auth/login/?screen_hint=sign-up")
        self.assertContains(response=response, text="Start for free")
        self.assertNotContains(response=response, text="Get a trial slot")
        self.assertNotContains(response=response, text='id="waitlist"')
