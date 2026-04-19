"""Unit tests for the mock PDP lambda handler."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import handler  # noqa: E402


def _event(body: dict, path: str = "/evaluate", method: str = "POST") -> dict:
    return {
        "httpMethod": method,
        "path": path,
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }


def _set_allowed(monkeypatch, csv: str) -> None:
    monkeypatch.setenv("ALLOWED_USERNAMES", csv)


def test_allow_for_username_in_list(monkeypatch) -> None:
    _set_allowed(monkeypatch, "alice,vmendi@example.com,bob")
    response = handler.handler(
        _event({"app_id": "x", "oidc_sub": "s", "username": "alice", "path": "/"}), None,
    )
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body == {"decision": "allow", "reason": "mock-allowlist"}


def test_deny_for_username_not_in_list(monkeypatch) -> None:
    _set_allowed(monkeypatch, "alice")
    response = handler.handler(
        _event({"app_id": "x", "oidc_sub": "s", "username": "bob", "path": "/"}), None,
    )
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["decision"] == "deny"
    assert body["reason"] == "mock-not-in-allowlist"


def test_missing_field_returns_400(monkeypatch) -> None:
    _set_allowed(monkeypatch, "alice")
    response = handler.handler(
        _event({"app_id": "x", "username": "alice"}), None,  # no oidc_sub / path
    )
    assert response["statusCode"] == 400


def test_invalid_json_returns_400(monkeypatch) -> None:
    _set_allowed(monkeypatch, "alice")
    response = handler.handler(
        {"httpMethod": "POST", "path": "/evaluate", "body": "not-json"}, None,
    )
    assert response["statusCode"] == 400


def test_wrong_method_is_404(monkeypatch) -> None:
    _set_allowed(monkeypatch, "alice")
    response = handler.handler(
        _event({"app_id": "x", "oidc_sub": "s", "username": "alice", "path": "/"}, method="GET"),
        None,
    )
    assert response["statusCode"] == 404


def test_wrong_path_is_404(monkeypatch) -> None:
    _set_allowed(monkeypatch, "alice")
    response = handler.handler(
        _event({"app_id": "x", "oidc_sub": "s", "username": "alice", "path": "/"}, path="/other"),
        None,
    )
    assert response["statusCode"] == 404


def test_empty_allowlist_denies_all(monkeypatch) -> None:
    _set_allowed(monkeypatch, "")
    response = handler.handler(
        _event({"app_id": "x", "oidc_sub": "s", "username": "alice", "path": "/"}), None,
    )
    body = json.loads(response["body"])
    assert body["decision"] == "deny"


def test_base64_body_is_decoded(monkeypatch) -> None:
    import base64
    _set_allowed(monkeypatch, "alice")
    body = json.dumps({"app_id": "x", "oidc_sub": "s", "username": "alice", "path": "/"})
    encoded = base64.b64encode(body.encode()).decode()
    response = handler.handler(
        {"httpMethod": "POST", "path": "/evaluate", "body": encoded, "isBase64Encoded": True},
        None,
    )
    assert response["statusCode"] == 200
