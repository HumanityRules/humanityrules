"""AppAdmin freezes App.environment on change.

Every historical deployment resolves its env via deployment.app.environment, so
editing an app's environment in the admin would silently rewire its whole history.
It must be settable on add and readonly on change.
"""

from django.contrib import admin
from django.test import RequestFactory, TestCase

from humanityrules_app.admin import AppAdmin
from humanityrules_app.models import App


class TestAppAdminEnvironmentReadonly(TestCase):
    def setUp(self) -> None:
        self.admin = AppAdmin(App, admin.site)
        self.request = RequestFactory().get("/admin/humanityrules_app/app/")

    def test_environment_editable_on_add(self) -> None:
        fields = self.admin.get_readonly_fields(self.request, obj=None)
        self.assertNotIn("environment", fields)

    def test_environment_readonly_on_change(self) -> None:
        fields = self.admin.get_readonly_fields(self.request, obj=App())
        self.assertIn("environment", fields)
