"""Admin protections for append-only billing records."""

from django.contrib import admin
from django.test import RequestFactory, SimpleTestCase

from humanityrules_app.admin import BillingUsageEventAdmin
from humanityrules_app.models import BillingUsageEvent


class TestBillingUsageEventAdmin(SimpleTestCase):
    def setUp(self) -> None:
        self.model_admin = BillingUsageEventAdmin(model=BillingUsageEvent, admin_site=admin.site)
        self.request = RequestFactory().get("/admin/humanityrules_app/billingusageevent/")

    def test_registered(self) -> None:
        self.assertTrue(admin.site.is_registered(BillingUsageEvent))

    def test_append_only(self) -> None:
        self.assertFalse(self.model_admin.has_add_permission(self.request))
        self.assertFalse(self.model_admin.has_change_permission(self.request))
        self.assertFalse(self.model_admin.has_delete_permission(self.request))
