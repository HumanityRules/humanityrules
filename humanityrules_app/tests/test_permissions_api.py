"""Tests for the bearer-auth /api/permissions/* endpoints (the Hermes editor).

The env-resident humr_broker relays the WebUI's calls here. These exercise the
JSON twin of the session-auth HTML editor: target resolution from the per-app
bearer alone (the body cannot rename the app or owner), statement mutation, the
draft/apply lifecycle, and ABAC on Apply. AWS-touching iam_utils calls are
mocked so the suite is hermetic.
"""

import json
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from humanityrules_app.services import permissions_service
from humanityrules_app.models import (
    AWSAccount,
    App,
    AppPermissionRequest,
    AppPermissions,
    Environment,
    Organization,
    OrganizationMembership,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.tests import bearer_test_helpers
from humanityrules_app.tests.app_test_factories import make_source_template


class TestPermissionsApi(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(
            name="Perm Org",
            slug="perm-org",
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://idp.example.com",
            oidc_client_id="cid",
            oidc_client_secret="csec",
        )
        self.aws_account = AWSAccount.objects.create(
            organization=self.org, name="Prod", aws_account_id="111122223333",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account,
            name="Default",
            slug="default",
            aws_region="us-east-1",
            shared_alb_hosted_zone="dev.example.com",
        )
        self.user = User.objects.create_user(
            username="vmendi", email="vmendi@example.com", password="pw", current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        self.workspace = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        self.app = self._make_app(slug="hermes", name="Hermes", owner_username=self.user.username)
        self.raw_token = bearer_test_helpers.make_app_bearer(app=self.app, raw="test-bearer")

        # No AWS in tests: empty policy baseline and empty resource listings.
        read_patch = patch(
            "humanityrules_app.services.permissions_service.iam_utils.read_app_permissions_policy", return_value=[],
        )
        list_patch = patch(
            "humanityrules_app.services.permissions_service.iam_utils.list_resources_for_services", return_value={},
        )
        read_patch.start()
        list_patch.start()
        self.addCleanup(read_patch.stop)
        self.addCleanup(list_patch.stop)

    def _make_app(self, slug: str, name: str, owner_username: str | None) -> App:
        app = App.objects.create(
            organization=self.org, workspace=self.workspace, source_template=make_source_template(),
            environment=self.env, name=name, slug=slug,
            container_port=8000, health_check_path="/health", cpu=256, memory=512,
        )
        if owner_username is not None:
            ResourceTag.objects.create(
                organization=self.org, resource_type=ResourceTag.ResourceType.APP,
                app=app, key="owner", value=owner_username,
            )
        return app

    def _post(self, path: str, payload: dict, bearer: str | None) -> object:
        headers = {}
        if bearer is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {bearer}"
        return self.client.post(path, data=json.dumps(payload), content_type="application/json", **headers)

    def _open_draft(self) -> dict:
        response = self._post("/api/permissions/draft", {}, bearer=self.raw_token)
        self.assertEqual(response.status_code, 200)
        return response.json()

    # ── Auth + target resolution ────────────────────────────────────────────

    def test_missing_bearer_returns_401(self) -> None:
        response = self._post("/api/permissions/draft", {}, bearer=None)
        self.assertEqual(response.status_code, 401)

    def test_invalid_bearer_returns_401(self) -> None:
        response = self._post("/api/permissions/draft", {}, bearer="nope")
        self.assertEqual(response.status_code, 401)

    def test_body_naming_another_app_acts_on_the_bearers_app(self) -> None:
        other = self._make_app(slug="other", name="Other", owner_username="someone-else")
        response = self._post(
            "/api/permissions/draft", {"owner_username": "someone-else", "app_slug": "other"}, bearer=self.raw_token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["app"], {"slug": "hermes", "name": "Hermes"})
        self.assertEqual(AppPermissionRequest.objects.filter(app=self.app).count(), 1)
        self.assertFalse(AppPermissionRequest.objects.filter(app=other).exists())

    def test_app_without_owner_returns_404(self) -> None:
        lonely = self._make_app(slug="lonely", name="Lonely", owner_username=None)
        lonely_token = bearer_test_helpers.make_app_bearer(app=lonely, raw="lonely-bearer")
        response = self._post("/api/permissions/draft", {}, bearer=lonely_token)
        self.assertEqual(response.status_code, 404)

    # ── Draft open + poll ─────────────────────────────────────────────────────

    def test_draft_resolve_creates_draft(self) -> None:
        draft = self._open_draft()
        self.assertEqual(draft["status"], "draft")
        self.assertFalse(draft["has_changes"])
        self.assertEqual(draft["service_groups"], [])
        self.assertEqual(draft["app"], {"slug": "hermes", "name": "Hermes"})
        self.assertEqual(draft["environment"]["slug"], "default")
        self.assertEqual(draft["environment"]["aws_account"], "111122223333")
        self.assertEqual(AppPermissionRequest.objects.filter(app=self.app).count(), 1)

    def test_draft_poll_by_request_id_returns_live_status(self) -> None:
        draft = self._open_draft()
        apr = AppPermissionRequest.objects.get(id=draft["request_id"])
        apr.status = AppPermissionRequest.Status.APPLYING
        apr.save(update_fields=["status"])
        response = self._post(
            "/api/permissions/draft", {"request_id": draft["request_id"]}, bearer=self.raw_token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "applying")
        # A bare resolve must NOT spawn a second request behind the in-flight one.
        self.assertEqual(AppPermissionRequest.objects.filter(app=self.app).count(), 1)

    def test_draft_poll_unknown_request_id_returns_404(self) -> None:
        response = self._post(
            "/api/permissions/draft",
            {"request_id": "00000000-0000-0000-0000-000000000000"},
            bearer=self.raw_token,
        )
        self.assertEqual(response.status_code, 404)

    # ── Statement mutation ─────────────────────────────────────────────────────

    def test_add_service_then_level(self) -> None:
        draft = self._open_draft()
        rid = draft["request_id"]
        r1 = self._post("/api/permissions/draft/statement", {"request_id": rid, "action": "add_service", "service": "s3"}, bearer=self.raw_token)
        self.assertEqual(r1.status_code, 200)
        groups = r1.json()["service_groups"]
        self.assertEqual([g["service"] for g in groups], ["s3"])
        sid = groups[0]["sid"]
        self.assertTrue(sid)

        r2 = self._post("/api/permissions/draft/statement", {"request_id": rid, "action": "add_level", "service": "s3", "statement_id": sid, "level": "Read"}, bearer=self.raw_token)
        self.assertEqual(r2.status_code, 200)
        body = r2.json()
        self.assertTrue(body["has_changes"])
        s3 = next(g for g in body["service_groups"] if g["sid"] == sid)
        read = next(lvl for lvl in s3["access_levels"] if lvl["name"] == "Read")
        self.assertTrue(read["checked"])

    def test_agent_upsert_keys_by_service_and_resources(self) -> None:
        apr = AppPermissionRequest.objects.create(
            app=self.app, status=AppPermissionRequest.Status.DRAFT,
        )
        table_arn = "arn:aws:dynamodb:us-east-1:111122223333:table/Orders"
        # Distinct resource sets → two statements; same resource set → merge levels.
        async_to_sync(permissions_service.aupsert_statement)(apr, "dynamodb", ["List"], ["*"])
        async_to_sync(permissions_service.aupsert_statement)(apr, "dynamodb", ["Read"], [table_arn])
        async_to_sync(permissions_service.aupsert_statement)(apr, "dynamodb", ["Write"], ["*"])

        apr.refresh_from_db()
        statements = apr.statements
        self.assertEqual(len(statements), 2)
        star = next(s for s in statements if s["resources"] == ["*"])
        self.assertEqual(sorted(star["access_levels"]), ["List", "Write"])
        table = next(s for s in statements if s["resources"] == [table_arn])
        self.assertEqual(table["access_levels"], ["Read"])
        self.assertTrue(all(s.get("sid") for s in statements))

    def test_two_statements_same_service_hold_distinct_scopes(self) -> None:
        draft = self._open_draft()
        rid = draft["request_id"]

        g1 = self._post("/api/permissions/draft/statement", {"request_id": rid, "action": "add_service", "service": "dynamodb"}, bearer=self.raw_token).json()["service_groups"]
        sid1 = g1[0]["sid"]
        groups = self._post("/api/permissions/draft/statement", {"request_id": rid, "action": "add_service", "service": "dynamodb"}, bearer=self.raw_token).json()["service_groups"]
        self.assertEqual(len(groups), 2)
        sid2 = next(g["sid"] for g in groups if g["sid"] != sid1)

        # List on the first statement, Read on the second — independent scopes.
        self._post("/api/permissions/draft/statement", {"request_id": rid, "action": "add_level", "service": "dynamodb", "statement_id": sid1, "level": "List"}, bearer=self.raw_token)
        self._post("/api/permissions/draft/statement", {"request_id": rid, "action": "add_resource", "service": "dynamodb", "statement_id": sid1, "arn": "*"}, bearer=self.raw_token)
        self._post("/api/permissions/draft/statement", {"request_id": rid, "action": "add_level", "service": "dynamodb", "statement_id": sid2, "level": "Read"}, bearer=self.raw_token)
        body = self._post("/api/permissions/draft/statement", {"request_id": rid, "action": "add_resource", "service": "dynamodb", "statement_id": sid2, "arn": "arn:aws:dynamodb:us-east-1:111122223333:table/Orders"}, bearer=self.raw_token).json()

        statements = AppPermissionRequest.objects.get(id=rid).statements
        self.assertEqual(len(statements), 2)
        by_sid = {s["sid"]: s for s in statements}
        self.assertEqual(by_sid[sid1]["access_levels"], ["List"])
        self.assertEqual(by_sid[sid1]["resources"], ["*"])
        self.assertEqual(by_sid[sid2]["access_levels"], ["Read"])
        self.assertEqual(by_sid[sid2]["resources"], ["arn:aws:dynamodb:us-east-1:111122223333:table/Orders"])
        self.assertEqual(len(body["service_groups"]), 2)

    def test_statement_on_non_draft_returns_409(self) -> None:
        draft = self._open_draft()
        rid = draft["request_id"]
        apr = AppPermissionRequest.objects.get(id=rid)
        apr.status = AppPermissionRequest.Status.APPLIED
        apr.save(update_fields=["status"])
        response = self._post(
            "/api/permissions/draft/statement",
            {"request_id": rid, "action": "add_service", "service": "s3"},
            bearer=self.raw_token,
        )
        self.assertEqual(response.status_code, 409)

    # ── Description / cancel ───────────────────────────────────────────────────

    def test_description_updates(self) -> None:
        draft = self._open_draft()
        rid = draft["request_id"]
        response = self._post(
            "/api/permissions/draft/description",
            {"request_id": rid, "description": "needs s3 read"},
            bearer=self.raw_token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(AppPermissionRequest.objects.get(id=rid).description, "needs s3 read")

    def test_cancel_resets_to_baseline(self) -> None:
        AppPermissions.objects.create(
            app=self.app,
            statements=[{"service": "sqs", "effect": "Allow", "access_levels": ["Read"], "resources": []}],
        )
        draft = self._open_draft()
        rid = draft["request_id"]
        apr = AppPermissionRequest.objects.get(id=rid)
        apr.statements = [{"service": "s3", "effect": "Allow", "access_levels": ["Write"], "resources": []}]
        apr.save(update_fields=["statements"])

        response = self._post("/api/permissions/draft/cancel", {"request_id": rid}, bearer=self.raw_token)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["has_changes"])
        self.assertEqual(
            [g["service"] for g in response.json()["service_groups"]], ["sqs"],
        )

    # ── Apply (ABAC) ───────────────────────────────────────────────────────────

    def test_apply_denied_returns_403(self) -> None:
        draft = self._open_draft()
        rid = draft["request_id"]
        with patch("humanityrules_app.views.permissions_api.abac_service.check_action", return_value=False):
            response = self._post("/api/permissions/draft/apply", {"request_id": rid}, bearer=self.raw_token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AppPermissionRequest.objects.get(id=rid).status, AppPermissionRequest.Status.DRAFT)

    def test_apply_approved_flips_status(self) -> None:
        draft = self._open_draft()
        rid = draft["request_id"]
        with patch("humanityrules_app.views.permissions_api.abac_service.check_action", return_value=True):
            response = self._post("/api/permissions/draft/apply", {"request_id": rid}, bearer=self.raw_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "approved_pending_apply", "request_id": rid})
        self.assertEqual(
            AppPermissionRequest.objects.get(id=rid).status, AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
        )

    def test_apply_does_not_requeue_applying_request(self) -> None:
        draft = self._open_draft()
        rid = draft["request_id"]
        AppPermissionRequest.objects.filter(id=rid).update(status=AppPermissionRequest.Status.APPLYING)

        with patch("humanityrules_app.views.permissions_api.abac_service.check_action", return_value=True):
            response = self._post(
                "/api/permissions/draft/apply",
                {"request_id": rid},
                bearer=self.raw_token,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(AppPermissionRequest.objects.get(id=rid).status, AppPermissionRequest.Status.APPLYING)

    def test_apply_on_sandbox_account_blocked_for_non_platform_owner_org(self) -> None:
        # ABAC allows (every signup is admin of their own org), but the env sits on
        # HumR's shared sandbox account — the sandbox gate must win.
        self.aws_account.is_humr_sandbox = True
        self.aws_account.save(update_fields=["is_humr_sandbox"])
        draft = self._open_draft()
        rid = draft["request_id"]
        with (
            self.settings(HUMR_PLATFORM_OWNER_ORG_SLUG="humanity-rules"),
            patch("humanityrules_app.views.permissions_api.abac_service.check_action", return_value=True),
        ):
            response = self._post("/api/permissions/draft/apply", {"request_id": rid}, bearer=self.raw_token)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Humanity Rules", response.json()["error"])
        self.assertEqual(AppPermissionRequest.objects.get(id=rid).status, AppPermissionRequest.Status.DRAFT)

    def test_apply_on_sandbox_account_allowed_for_platform_owner_org(self) -> None:
        self.aws_account.is_humr_sandbox = True
        self.aws_account.save(update_fields=["is_humr_sandbox"])
        draft = self._open_draft()
        rid = draft["request_id"]
        with (
            self.settings(HUMR_PLATFORM_OWNER_ORG_SLUG="perm-org"),
            patch("humanityrules_app.views.permissions_api.abac_service.check_action", return_value=True),
        ):
            response = self._post("/api/permissions/draft/apply", {"request_id": rid}, bearer=self.raw_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            AppPermissionRequest.objects.get(id=rid).status, AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
        )

    # ── Catalog / resources ────────────────────────────────────────────────────

    def test_service_catalog(self) -> None:
        response = self._post("/api/permissions/service-catalog", {}, bearer=self.raw_token)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("Read", body["access_levels"])
        self.assertTrue(any(s["value"] == "s3" and s["is_curated"] for s in body["services"]))

    def test_resources_requires_service(self) -> None:
        response = self._post("/api/permissions/resources", {}, bearer=self.raw_token)
        self.assertEqual(response.status_code, 400)

    def test_resources_returns_available(self) -> None:
        response = self._post(
            "/api/permissions/resources", {"service": "s3"}, bearer=self.raw_token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"service": "s3", "available_resources": []})
