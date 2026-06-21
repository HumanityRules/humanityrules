"""Tests for the User model — username immutability."""

from django.test import TestCase

from humanityrules_app.models import Organization, User


class TestUsernameImmutable(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="UserOrg", slug="userorg")

    def test_can_set_username_on_create(self) -> None:
        user = User.objects.create_user(
            username="vmendi", password="pw", current_organization=self.org,
        )
        self.assertEqual(user.username, "vmendi")

    def test_username_cannot_change_after_create(self) -> None:
        user = User.objects.create_user(
            username="vmendi", password="pw", current_organization=self.org,
        )
        user.username = "someone-else"
        with self.assertRaises(ValueError) as cm:
            user.save()
        self.assertIn("immutable", str(cm.exception))

    def test_other_fields_still_editable(self) -> None:
        user = User.objects.create_user(
            username="vmendi", password="pw", current_organization=self.org,
        )
        user.email = "new@example.com"
        user.first_name = "Victor"
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.email, "new@example.com")
        self.assertEqual(user.first_name, "Victor")
        self.assertEqual(user.username, "vmendi")
