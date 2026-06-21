"""Tests for the humr_broker permissions relay (permissions_control.py).

The relay runs inside the customer-env Hermes container (not Django). It is pure
transport: it folds the browser path-param/query into a JSON payload and forwards
to DOH via DohClient.post_json, attaching no bearer and making no decision. We
load the module directly and drive its routes with a stub DohClient + Starlette
TestClient, without standing up the broker's asyncio servers.
"""

import importlib.util
import pathlib
import sys
import types
import unittest

from starlette.applications import Starlette
from starlette.testclient import TestClient


def _load_permissions_control() -> types.ModuleType:
    """Load template_repos/.../integrations/permissions_control.py with its dir on sys.path."""
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    integrations_dir = repo_root / "template_repos" / "hermes_agent" / "humr_runtime" / "integrations"
    if str(integrations_dir) not in sys.path:
        sys.path.insert(0, str(integrations_dir))
    spec = importlib.util.spec_from_file_location(
        name="permissions_control_under_test", location=str(integrations_dir / "permissions_control.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["permissions_control_under_test"] = module
    spec.loader.exec_module(module)
    return module


permissions_control = _load_permissions_control()


class _StubDohClient:
    """Records post_json calls and returns a canned (status, body)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.response: tuple[int, dict] = (200, {"ok": True})

    async def post_json(self, path: str, payload: dict, timeout_seconds: int) -> tuple[int, dict]:
        self.calls.append({"path": path, "payload": payload, "timeout_seconds": timeout_seconds})
        return self.response


class TestPermissionsControlRelay(unittest.TestCase):

    def _client(self, humr_client: _StubDohClient) -> TestClient:
        app = Starlette(routes=permissions_control.routes(prefix="/permissions", humr_client=humr_client))
        return TestClient(app)

    def test_draft_open_forwards_empty_payload(self) -> None:
        stub = _StubDohClient()
        with self._client(stub) as client:
            resp = client.get("/permissions/draft")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(stub.calls[0]["path"], "/api/permissions/draft")
        self.assertEqual(stub.calls[0]["payload"], {})

    def test_draft_poll_forwards_request_id_from_query(self) -> None:
        stub = _StubDohClient()
        with self._client(stub) as client:
            client.get("/permissions/draft", params={"request_id": "R1"})
        self.assertEqual(stub.calls[0]["path"], "/api/permissions/draft")
        self.assertEqual(stub.calls[0]["payload"], {"request_id": "R1"})

    def test_statement_folds_path_request_id_into_body(self) -> None:
        stub = _StubDohClient()
        with self._client(stub) as client:
            client.post("/permissions/draft/R1/statement", json={"action": "add_service", "service": "s3"})
        self.assertEqual(stub.calls[0]["path"], "/api/permissions/draft/statement")
        self.assertEqual(
            stub.calls[0]["payload"], {"action": "add_service", "service": "s3", "request_id": "R1"},
        )

    def test_apply_forwards_only_request_id(self) -> None:
        stub = _StubDohClient()
        with self._client(stub) as client:
            client.post("/permissions/draft/R9/apply")
        self.assertEqual(stub.calls[0]["path"], "/api/permissions/draft/apply")
        self.assertEqual(stub.calls[0]["payload"], {"request_id": "R9"})

    def test_resources_forwards_service_from_query(self) -> None:
        stub = _StubDohClient()
        with self._client(stub) as client:
            client.get("/permissions/resources", params={"service": "sqs"})
        self.assertEqual(stub.calls[0]["path"], "/api/permissions/resources")
        self.assertEqual(stub.calls[0]["payload"], {"service": "sqs"})

    def test_status_and_body_pass_through_verbatim(self) -> None:
        stub = _StubDohClient()
        stub.response = (409, {"error": "permission request is not a draft"})
        with self._client(stub) as client:
            resp = client.post("/permissions/draft/R1/statement", json={"action": "add_service", "service": "s3"})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json(), {"error": "permission request is not a draft"})
