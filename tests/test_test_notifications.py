"""Optional test-notification panel: config, validation, listener errors."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

import app.server as server
import app.test_notifications as tn


@pytest.fixture
def local():
    return TestClient(server.app, base_url="http://127.0.0.1:8090")


@pytest.fixture(autouse=True)
def fresh_session():
    server.session.reset()
    yield
    server.session.reset()


def _configure(monkeypatch, url="https://api.moveworks.ai/webhooks/v1/listeners/l1/notify", secret=""):
    monkeypatch.setattr(tn, "listener_config", lambda: {"url": url, "secret": secret, "email": "me@example.com"})


def test_status_reports_routing_state(local, monkeypatch):
    _configure(monkeypatch)
    assert local.get("/api/test-notification").json() == {
        "configured": True, "secured": False, "email": "me@example.com", "seconds_since_last_message": None,
    }


def test_unconfigured_listener_is_a_clear_409(local, monkeypatch):
    _configure(monkeypatch, url="")
    resp = local.post("/api/test-notification", json={"email": "me@example.com", "message": "hi"})
    assert resp.status_code == 409
    assert "MW_TEST_LISTENER_URL" in resp.json()["detail"]


def test_sends_email_and_message_to_the_listener(local, monkeypatch):
    _configure(monkeypatch)
    sent = []
    monkeypatch.setattr(tn, "send_to_listener", lambda url, secret, payload: sent.append((url, payload)) or {"status": 200, "body": ""})

    resp = local.post("/api/test-notification", json={"email": " me@example.com ", "message": " ping "})

    assert resp.status_code == 200
    assert sent == [("https://api.moveworks.ai/webhooks/v1/listeners/l1/notify", {"email": "me@example.com", "message": "ping"})]


@pytest.mark.parametrize("status, expected", [(429, 429), (401, 502)])
def test_listener_errors_are_surfaced(local, monkeypatch, status, expected):
    _configure(monkeypatch)
    monkeypatch.setattr(tn, "send_to_listener", lambda *a: {"status": status, "body": "nope"})
    assert local.post("/api/test-notification", json={"email": "a@b.c", "message": "x"}).status_code == expected


def test_signed_request_carries_an_hmac_of_the_exact_body():
    captured = {}

    def post(url, data, headers, timeout):
        captured.update(data=data, headers=headers)
        return type("R", (), {"status_code": 200, "text": ""})()

    tn.send_to_listener("https://example.com/l", "s3cret", {"email": "a@b.c", "message": "x"}, post=post)

    expected = hmac.new(b"s3cret", captured["data"], hashlib.sha256).hexdigest()
    assert captured["headers"][tn.SIGNATURE_HEADER] == expected


def test_unsecured_request_is_not_signed():
    captured = {}
    tn.send_to_listener("https://example.com/l", "", {"email": "a@b.c", "message": "x"},
                        post=lambda url, data, headers, timeout: captured.update(headers=headers) or type("R", (), {"status_code": 200, "text": ""})())
    assert tn.SIGNATURE_HEADER not in captured["headers"]
