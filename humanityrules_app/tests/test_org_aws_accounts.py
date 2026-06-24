"""Tests for the org-admin AWS Accounts UI: the Edit modal flow, the connect poll,
and the record-only disconnect.

Covers org-admin gating, the edit modal popping up in both connected and pending
states, the rename POST, validation (blank/duplicate name), multi-tenant isolation,
the connect modal's self-terminating poll, the record-only disconnect, and the
environments-still-attached guard.
"""

import urllib.parse

from django.test import TestCase
from django.utils.html import escape

from humanityrules_app.models import AWSAccount, Environment, Organization, User
from humanityrules_app.services import abac_service

HTMX = {"HTTP_HX_REQUEST": "true"}


class AWSAccountsUITestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="AWS Org", slug="aws-org")
        self.admin = User.objects.create_user(username="admin", password="pw", current_organization=self.org)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin)

        self.member = User.objects.create_user(username="member", password="pw", current_organization=self.org)
        abac_service.materialize_membership(organization=self.org, user=self.member, role="member")

    def _account(self, name: str, status: str) -> AWSAccount:
        return AWSAccount.objects.create(organization=self.org, name=name, status=status, created_by=self.admin)

    def _connected_account(self, name: str) -> AWSAccount:
        return AWSAccount.objects.create(
            organization=self.org, name=name, status=AWSAccount.Status.CONNECTED,
            aws_account_id="123456789012", role_arn="arn:aws:iam::123456789012:role/humr-x", created_by=self.admin,
        )

    def _edit_url(self, account: AWSAccount) -> str:
        return f"/integrations/org/aws-accounts/{account.id}/edit/"

    def _disconnect_confirm_url(self, account: AWSAccount) -> str:
        return f"/integrations/org/aws-accounts/{account.id}/disconnect-confirm/"

    def _disconnect_url(self, account: AWSAccount) -> str:
        return f"/integrations/org/aws-accounts/{account.id}/disconnect/"


class TestAccessControl(AWSAccountsUITestBase):

    def test_member_forbidden(self) -> None:
        self.client.force_login(self.member)
        account = self._account(name="Production", status=AWSAccount.Status.CONNECTED)
        self.assertEqual(self.client.get(self._edit_url(account)).status_code, 403)

    def test_admin_allowed(self) -> None:
        self.client.force_login(self.admin)
        account = self._account(name="Production", status=AWSAccount.Status.CONNECTED)
        self.assertEqual(self.client.get(self._edit_url(account)).status_code, 200)


class TestEditModalRenders(AWSAccountsUITestBase):

    def test_pops_up_in_connected_state(self) -> None:
        self.client.force_login(self.admin)
        account = self._account(name="Production", status=AWSAccount.Status.CONNECTED)
        body = self.client.get(self._edit_url(account)).content.decode()
        self.assertIn("Edit AWS Account", body)
        self.assertIn("Connected", body)
        self.assertIn('value="Production"', body)

    def test_pops_up_in_pending_state_with_setup_link(self) -> None:
        self.client.force_login(self.admin)
        account = self._account(name="Staging", status=AWSAccount.Status.PENDING)
        body = self.client.get(self._edit_url(account)).content.decode()
        self.assertIn("Edit AWS Account", body)
        self.assertIn("Pending", body)
        # Pending accounts offer the CloudFormation authorization page to finish setup,
        # with the callback pointed back at the host the admin reached us on (the test client).
        self.assertIn(escape(account.get_cloudformation_url(api_endpoint="http://testserver")), body)


class TestEditPost(AWSAccountsUITestBase):

    def test_renames_account(self) -> None:
        self.client.force_login(self.admin)
        account = self._account(name="Production", status=AWSAccount.Status.CONNECTED)
        response = self.client.post(self._edit_url(account), data={"name": "Prod (US)"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "awsAccountsChanged")
        account.refresh_from_db()
        self.assertEqual(account.name, "Prod (US)")

    def test_blank_name_rerenders_with_error(self) -> None:
        self.client.force_login(self.admin)
        account = self._account(name="Production", status=AWSAccount.Status.CONNECTED)
        response = self.client.post(self._edit_url(account), data={"name": "  "})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Trigger", response)
        self.assertIn("Please enter an AWS account name", response.content.decode())
        account.refresh_from_db()
        self.assertEqual(account.name, "Production")

    def test_duplicate_name_rejected(self) -> None:
        self.client.force_login(self.admin)
        self._account(name="Staging", status=AWSAccount.Status.CONNECTED)
        account = self._account(name="Production", status=AWSAccount.Status.CONNECTED)
        response = self.client.post(self._edit_url(account), data={"name": "Staging"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Trigger", response)
        self.assertIn("already exists", response.content.decode())
        account.refresh_from_db()
        self.assertEqual(account.name, "Production")

    def test_unchanged_name_is_allowed(self) -> None:
        self.client.force_login(self.admin)
        account = self._account(name="Production", status=AWSAccount.Status.CONNECTED)
        response = self.client.post(self._edit_url(account), data={"name": "Production"})
        self.assertEqual(response["HX-Trigger"], "awsAccountsChanged")
        account.refresh_from_db()
        self.assertEqual(account.name, "Production")


class TestMultiTenancy(AWSAccountsUITestBase):

    def setUp(self) -> None:
        super().setUp()
        self.other_org = Organization.objects.create(name="Other Org", slug="other-org")
        self.other_account = AWSAccount.objects.create(organization=self.other_org, name="Theirs", status=AWSAccount.Status.CONNECTED)

    def test_cannot_edit_other_orgs_account(self) -> None:
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self._edit_url(self.other_account)).status_code, 404)
        self.client.post(self._edit_url(self.other_account), data={"name": "Renamed"})
        self.other_account.refresh_from_db()
        self.assertEqual(self.other_account.name, "Theirs")

    def test_cannot_disconnect_other_orgs_account(self) -> None:
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self._disconnect_confirm_url(self.other_account)).status_code, 404)
        self.assertEqual(self.client.post(self._disconnect_url(self.other_account)).status_code, 404)
        self.other_account.refresh_from_db()
        self.assertEqual(self.other_account.status, AWSAccount.Status.CONNECTED)


class TestDisconnectView(AWSAccountsUITestBase):

    def test_member_forbidden(self) -> None:
        self.client.force_login(self.member)
        account = self._connected_account(name="Production")
        self.assertEqual(self.client.get(self._disconnect_confirm_url(account)).status_code, 403)
        self.assertEqual(self.client.post(self._disconnect_url(account)).status_code, 403)

    def test_confirm_modal_explains_record_only_and_names_the_stack(self) -> None:
        self.client.force_login(self.admin)
        account = self._connected_account(name="Production")
        body = self.client.get(self._disconnect_confirm_url(account)).content.decode()
        self.assertIn("Disconnect AWS Account", body)
        # Names the account and the stack the customer must delete to fully revoke access.
        self.assertIn("123456789012", body)
        self.assertIn(account.get_install_stack_name(), body)
        # Makes clear we leave the stack in place / do not touch their account.
        self.assertIn("does not touch your AWS account", body)
        self.assertIn(self._disconnect_url(account), body)

    def test_post_removes_record(self) -> None:
        self.client.force_login(self.admin)
        account = self._connected_account(name="Production")
        response = self.client.post(self._disconnect_url(account), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AWSAccount.objects.filter(id=account.id).exists())

    def test_post_pushes_accounts_list_url(self) -> None:
        # The confirm modal posts with hx-push-url; the address bar must land on the list,
        # not the POST-only disconnect URL (which 405s on reload).
        self.client.force_login(self.admin)
        account = self._connected_account(name="Production")
        response = self.client.post(self._disconnect_url(account), **HTMX)
        self.assertEqual(response["HX-Push-Url"], "/integrations/org/aws-accounts/")

    def test_get_confirm_does_not_remove_record(self) -> None:
        self.client.force_login(self.admin)
        account = self._connected_account(name="Production")
        self.client.get(self._disconnect_confirm_url(account))
        self.assertTrue(AWSAccount.objects.filter(id=account.id).exists())

    def test_blocked_while_environments_attached(self) -> None:
        self.client.force_login(self.admin)
        account = self._connected_account(name="Production")
        Environment.objects.create(aws_account=account, name="Dev", slug="dev", aws_region="us-east-1")
        self.assertEqual(self.client.get(self._disconnect_confirm_url(account)).status_code, 403)
        self.assertEqual(self.client.post(self._disconnect_url(account)).status_code, 403)
        self.assertTrue(AWSAccount.objects.filter(id=account.id).exists())


class TestConnectPolling(AWSAccountsUITestBase):
    """The connect modal arms a self-terminating poll and closes once the account connects."""

    ADD_URL = "/integrations/org/aws-accounts/add/"

    def _status_url(self, account: AWSAccount) -> str:
        return f"/integrations/org/aws-accounts/{account.id}/status/"

    def test_add_post_arms_poll_and_opens_cloudformation(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(self.ADD_URL, data={"name": "New Account"}, **HTMX)
        self.assertEqual(response.status_code, 200)
        account = AWSAccount.objects.get(organization=self.org, name="New Account")
        self.assertEqual(account.status, AWSAccount.Status.PENDING)
        body = response.content.decode()
        self.assertIn("aws-connect-poll", body)
        self.assertIn(self._status_url(account), body)
        # Interval trigger (not self-replacing load) so a hidden poller re-fires reliably.
        self.assertIn("every 30s", body)
        self.assertIn("openCloudFormation", response["HX-Trigger"])

    def test_status_keeps_polling_while_pending(self) -> None:
        self.client.force_login(self.admin)
        account = self._account(name="Pending", status=AWSAccount.Status.PENDING)
        response = self.client.get(self._status_url(account), **HTMX)
        # 204 = nothing to swap; the modal's `every 30s` interval keeps running on its own.
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        # Must NOT close the modal or re-open the CloudFormation tab.
        self.assertNotIn("HX-Retarget", response)
        self.assertNotIn("HX-Trigger", response)

    def test_status_closes_modal_and_refreshes_once_connected(self) -> None:
        self.client.force_login(self.admin)
        account = self._connected_account(name="Production")
        response = self.client.get(self._status_url(account), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "")
        self.assertEqual(response["HX-Retarget"], "#modal-container")
        self.assertEqual(response["HX-Trigger"], "awsAccountsChanged")

    def test_status_member_forbidden(self) -> None:
        self.client.force_login(self.member)
        account = self._account(name="Pending", status=AWSAccount.Status.PENDING)
        self.assertEqual(self.client.get(self._status_url(account)).status_code, 403)

    def test_status_for_other_orgs_account_closes_without_leaking(self) -> None:
        other_org = Organization.objects.create(name="Other", slug="other")
        other = AWSAccount.objects.create(organization=other_org, name="Theirs", status=AWSAccount.Status.PENDING)
        self.client.force_login(self.admin)
        response = self.client.get(self._status_url(other), **HTMX)
        self.assertEqual(response["HX-Retarget"], "#modal-container")
        self.assertNotIn("Theirs", response.content.decode())


class TestCloudFormationUrl(AWSAccountsUITestBase):
    """The quick-create link carries the external id and the issuing control plane's callback endpoint."""

    def _params(self, url: str) -> dict:
        # Quick-create params live in the URL fragment: ...#/stacks/quickcreate?param_...
        fragment = urllib.parse.urlparse(url).fragment
        return urllib.parse.parse_qs(fragment.split("?", 1)[1])

    def test_url_carries_external_id_and_given_endpoint(self) -> None:
        # param_ApiEndpoint lets the shared install Lambda call back to whichever control
        # plane issued the link, instead of always hitting prod.
        account = self._account(name="Production", status=AWSAccount.Status.PENDING)
        params = self._params(account.get_cloudformation_url(api_endpoint="https://humanityrules.ngrok.io"))
        self.assertEqual(params["param_ExternalId"], [str(account.external_id)])
        self.assertEqual(params["param_ApiEndpoint"], ["https://humanityrules.ngrok.io"])

    def test_add_flow_bakes_in_the_request_host(self) -> None:
        # No configuration: the callback endpoint is derived from the host the admin used,
        # so a dev box behind ngrok issues links that point back at the dev box.
        self.client.force_login(self.admin)
        response = self.client.post(
            "/integrations/org/aws-accounts/add/", data={"name": "From Ngrok"},
            HTTP_HOST="humanityrules.ngrok.io", **HTMX,
        )
        account = AWSAccount.objects.get(organization=self.org, name="From Ngrok")
        trigger_url = response["HX-Trigger"].split('"openCloudFormation": "', 1)[1].rstrip('"}')
        params = self._params(trigger_url)
        self.assertEqual(params["param_ApiEndpoint"], ["http://humanityrules.ngrok.io"])
        self.assertEqual(params["param_ExternalId"], [str(account.external_id)])


class TestAccountsListPoll(AWSAccountsUITestBase):
    """The accounts list self-polls every 30s while any account is pending, and stops otherwise."""

    LIST_URL = "/integrations/org/aws-accounts/"

    def test_list_polls_while_a_pending_account_exists(self) -> None:
        self.client.force_login(self.admin)
        self._account(name="Pending", status=AWSAccount.Status.PENDING)
        body = self.client.get(self.LIST_URL, **HTMX).content.decode()
        self.assertIn("every 30s", body)

    def test_list_does_not_poll_when_all_connected(self) -> None:
        self.client.force_login(self.admin)
        self._connected_account(name="Production")
        body = self.client.get(self.LIST_URL, **HTMX).content.decode()
        self.assertNotIn("every 30s", body)

    def test_close_button_refreshes_the_list(self) -> None:
        # Closing the add modal fires awsAccountsChanged so the list re-renders and shows the new
        # pending row (whose own poll then takes over).
        self.client.force_login(self.admin)
        body = self.client.get("/integrations/org/aws-accounts/add/", **HTMX).content.decode()
        self.assertIn("awsAccountsChanged", body)


class TestExternalIdAndStackName(AWSAccountsUITestBase):
    """The install stack name derives from the random external_id, not the time-ordered id."""

    def test_install_stack_name_derives_from_external_id(self) -> None:
        # Random external_id (not the time-ordered id) keeps the stack name collision-free for
        # accounts onboarded into the same AWS account within the same minute.
        account = self._account(name="Production", status=AWSAccount.Status.PENDING)
        self.assertEqual(account.get_install_stack_name(), f"HumanityRules-{account.external_id.hex[:8]}")
