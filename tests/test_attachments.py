"""Optional file attachments: the upload endpoint and its protections."""

from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import app.server as server
from moveworks_capi.errors import RateLimitedError, RequestRejectedError

attachments = pytest.importorskip("app.attachments", reason="the attachments module was deleted, or the starter has no Files API helper")
from moveworks_capi.files import UploadedFile  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
UPLOAD_HEADERS = {"Content-Type": "application/octet-stream", "X-File-Name": quote("vpn error.png")}


@pytest.fixture
def local():
    return TestClient(server.app, base_url="http://127.0.0.1:8090")


@pytest.fixture
def uploads(monkeypatch):
    """Uploads the endpoint forwarded, answered by ``reply`` (a File or an exception)."""
    sent = []
    timeouts = []
    state = {"reply": None, "timeouts": timeouts}

    def fake_upload(client, file, *, filename=None, purpose="CONVERSATIONS_ATTACHMENT", timeout=None):
        sent.append((filename, file.read()))
        timeouts.append(timeout)
        if isinstance(state["reply"], Exception):
            raise state["reply"]
        return state["reply"] or UploadedFile(
            id="file_1", name=filename, content_type="image/png", size_bytes=len(PNG),
            purpose=purpose, create_time="2026-01-01T00:00:00Z", expire_time="2026-01-02T00:00:00Z",
        )

    monkeypatch.setattr(attachments, "upload_file", fake_upload)
    server.session.reset()
    server.session.client = object()
    yield sent, state
    server.session.reset()


class TestUpload:
    def test_the_capability_check_answers_when_the_module_is_registered(self, local):
        assert local.get("/api/files").json() == {"enabled": True}

    def test_a_file_is_forwarded_and_its_resource_returned(self, local, uploads):
        sent, _ = uploads

        resp = local.post("/api/files", content=PNG, headers=UPLOAD_HEADERS)

        assert resp.status_code == 200
        assert resp.json() == {"id": "file_1", "name": "vpn error.png", "content_type": "image/png",
                               "size_bytes": len(PNG), "expire_time": "2026-01-02T00:00:00Z"}
        assert sent == [("vpn error.png", PNG)]

    @pytest.mark.parametrize("status, message", [
        (413, "request rejected (413): 413 the file exceeds the 100 MB limit"),
        (422, "request rejected (422): 422 the file content failed validation"),
        (400, "request rejected (400): 400 the filename is invalid"),
    ])
    def test_api_rejections_keep_their_status_and_message(self, local, uploads, status, message):
        _, state = uploads
        state["reply"] = RequestRejectedError(message, status_code=status)

        resp = local.post("/api/files", content=PNG, headers=UPLOAD_HEADERS)

        assert resp.status_code == status
        assert resp.json()["detail"] == message

    def test_a_404_explains_that_the_files_api_is_enabled_per_instance(self, local, uploads):
        _, state = uploads
        state["reply"] = RequestRejectedError("request rejected (404): 404 not found", status_code=404)

        resp = local.post("/api/files", content=PNG, headers=UPLOAD_HEADERS)

        assert resp.status_code == 502
        assert "alpha and enabled per Moveworks instance" in resp.json()["detail"]

    def test_rate_limits_are_reported(self, local, uploads):
        _, state = uploads
        state["reply"] = RateLimitedError("rate limited")

        assert local.post("/api/files", content=PNG, headers=UPLOAD_HEADERS).status_code == 429

    def test_an_oversize_file_is_refused_without_forwarding(self, local, uploads, monkeypatch):
        sent, _ = uploads
        monkeypatch.setattr(attachments, "MAX_UPLOAD_BYTES", len(PNG) - 1)

        resp = local.post("/api/files", content=PNG, headers=UPLOAD_HEADERS)

        assert resp.status_code == 413
        assert "100 MB" in resp.json()["detail"]
        assert sent == []

    def test_an_oversize_body_is_refused_while_streaming_without_a_length(self, local, uploads, monkeypatch):
        sent, _ = uploads
        monkeypatch.setattr(attachments, "MAX_UPLOAD_BYTES", 100)

        def chunks():
            for _ in range(5):
                yield b"x" * 40

        resp = local.post("/api/files", content=chunks(), headers=UPLOAD_HEADERS)

        assert resp.status_code == 413
        assert sent == []

    def test_a_slow_upload_gets_a_long_timeout(self, local, uploads):
        _, state = uploads

        local.post("/api/files", content=PNG, headers=UPLOAD_HEADERS)

        assert state["timeouts"] == [attachments.UPLOAD_TIMEOUT_SECONDS]

    @pytest.mark.parametrize("name", [
        "line\nbreak.png", "tab\there.png", "evil\u202egnp.exe", "..", ".", "a/b.png", "a\\b.png", "x" * 256, "   ",
    ])
    def test_names_that_break_the_filename_rule_are_refused(self, local, uploads, name):
        resp = local.post("/api/files", content=PNG, headers={**UPLOAD_HEADERS, "X-File-Name": quote(name)})

        assert resp.status_code == 400
        assert uploads[0] == []

    def test_a_name_at_the_limit_after_normalization_is_accepted(self, local, uploads):
        # "e" + combining acute is two code points that NFC turns into one.
        name = "e\u0301" * 255

        assert local.post("/api/files", content=PNG, headers={**UPLOAD_HEADERS, "X-File-Name": quote(name)}).status_code == 200

    def test_the_library_s_own_checks_become_400s(self, local, uploads):
        _, state = uploads
        state["reply"] = ValueError("filename must not contain a double quote")

        resp = local.post("/api/files", content=PNG, headers={**UPLOAD_HEADERS, "X-File-Name": quote('say "hi".png')})

        assert resp.status_code == 400
        assert resp.json()["detail"] == "filename must not contain a double quote"

    def test_a_browser_that_disconnects_mid_upload_forwards_nothing(self, uploads):
        import asyncio

        sent_messages = []
        incoming = [
            {"type": "http.request", "body": b"x" * 100, "more_body": True},
            {"type": "http.disconnect"},
        ]

        async def receive():
            return incoming.pop(0) if incoming else {"type": "http.disconnect"}

        async def send(message):
            sent_messages.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/api/files", "raw_path": b"/api/files", "query_string": b"",
            "root_path": "", "server": ("127.0.0.1", 8090), "client": ("127.0.0.1", 50000),
            "headers": [(b"host", b"127.0.0.1:8090"), (b"content-type", b"application/octet-stream"),
                        (b"x-file-name", b"a.png")],
        }

        asyncio.run(server.app(scope, receive, send))

        assert uploads[0] == []

    def test_an_empty_file_is_refused(self, local, uploads):
        sent, _ = uploads

        assert local.post("/api/files", content=b"", headers=UPLOAD_HEADERS).status_code == 400
        assert sent == []

    def test_uploading_needs_a_connection(self, local, uploads):
        server.session.reset()

        assert local.post("/api/files", content=PNG, headers=UPLOAD_HEADERS).status_code == 409


class TestUploadProtections:
    def test_foreign_host_header_is_refused(self, uploads):
        rebound = TestClient(server.app, base_url="http://attacker.example:8090")

        assert rebound.post("/api/files", content=PNG, headers=UPLOAD_HEADERS).status_code == 400
        assert uploads[0] == []

    def test_cross_origin_upload_is_refused(self, local, uploads):
        resp = local.post("/api/files", content=PNG, headers={**UPLOAD_HEADERS, "Origin": "https://attacker.example"})

        assert resp.status_code == 403
        assert uploads[0] == []

    @pytest.mark.parametrize("headers", [
        {"Content-Type": "application/octet-stream"},
        {"Content-Type": "multipart/form-data; boundary=x", "X-File-Name": "a.png"},
        {"Content-Type": "text/plain", "X-File-Name": "a.png"},
    ])
    def test_only_raw_bytes_with_a_file_name_header_are_accepted(self, local, uploads, headers):
        # A form or plain-text POST needs no CORS preflight; the custom header does.
        assert local.post("/api/files", content=PNG, headers=headers).status_code == 415
        assert uploads[0] == []
