"""Optional wire capture: redaction, size caps, and which pane row gets each exchange."""

from __future__ import annotations

import asyncio
import functools
import io
import json
from urllib.parse import urlsplit

import pytest
import requests
from fastapi.testclient import TestClient
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict

import app.server as server
import app.turns as turns
import app.wire_capture as wire_capture
from moveworks_capi.auth import ApiKeyBearer
from moveworks_capi.client import ConversationsApiClient

BASE = "https://api.example.com"
SECRET_KEY = "mw-key-do-not-show"
SSE = (
    b'event: RESPONSE_CREATED\ndata: {"event_type": "RESPONSE_CREATED", "sequence_number": 0, '
    b'"response": {"response_id": "resp_1", "conversation_id": "conv_1"}}\n\n'
    b'event: RESPONSE_COMPLETED\ndata: {"event_type": "RESPONSE_COMPLETED", "sequence_number": 1, '
    b'"response": {"response_id": "resp_1", "status": "COMPLETED"}}\n\n'
)
COMPLETED = {"response_id": "resp_1", "conversation_id": "conv_1", "status": "COMPLETED", "outputs": []}


class FakeApi(HTTPAdapter):
    """Answers by (method, path) from a table instead of the network."""

    def __init__(self, routes):
        super().__init__()
        self.routes = routes

    def send(self, request, stream=False, **kwargs):
        status, headers, body = self.routes[(request.method, urlsplit(request.url).path)]
        response = requests.Response()
        response.status_code = status
        response.reason = "OK" if status < 400 else "Error"
        response.headers = CaseInsensitiveDict(headers)
        response.raw = io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())
        response.url = request.url
        response.request = request
        return response


def _session(routes) -> wire_capture.RecordingSession:
    session = wire_capture.RecordingSession()
    session.mount("https://", FakeApi(routes))
    return session


def _client(routes) -> ConversationsApiClient:
    return ConversationsApiClient(BASE, "Demo Bot", ApiKeyBearer(SECRET_KEY), max_retries=0, session=_session(routes))


def _wire_frames(raw: list) -> list:
    frames = []
    for chunk in raw:
        event, data = chunk.decode().strip().split("\n", 1)
        if event == "event: wire":
            frames.append(json.loads(data.removeprefix("data: ")))
    return frames


@pytest.fixture(autouse=True)
def capture_on(monkeypatch):
    monkeypatch.setitem(wire_capture.state, "enabled", True)
    wire_capture.hub.clear()
    yield
    wire_capture.hub.clear()


class TestRedaction:
    def test_credential_headers_are_redacted(self):
        shown = wire_capture.redact_headers({
            "Authorization": f"Bearer {SECRET_KEY}", "Cookie": "sid=1", "Set-Cookie": "sid=2",
            "X-Signature": "abc123", "X-Api-Key": "k", "Assistant-Name": "Demo Bot", "Content-Type": "application/json",
        })

        assert shown == {
            "Authorization": "[redacted]", "Cookie": "[redacted]", "Set-Cookie": "[redacted]",
            "X-Signature": "[redacted]", "X-Api-Key": "[redacted]",
            "Assistant-Name": "Demo Bot", "Content-Type": "application/json",
        }

    def test_token_exchange_form_body_is_redacted(self):
        body = "grant_type=client_credentials&client_assertion=eyJhbGciOi.eyJzdWIi.c2ln&client_secret=s3cret&assertion=a1"

        shown = wire_capture.format_body(body, "application/x-www-form-urlencoded")

        assert "grant_type=client_credentials" in shown
        assert "s3cret" not in shown and "eyJ" not in shown and "a1" not in shown
        assert shown.count("[redacted]") == 3

    def test_token_fields_in_json_bodies_are_redacted_at_any_depth(self):
        body = json.dumps({"access_token": "tok", "token_type": "Bearer", "nested": [{"refresh_token": "r", "id_token": "i"}]})

        shown = json.loads(wire_capture.format_body(body.encode(), "application/json"))

        assert shown == {"access_token": "[redacted]", "token_type": "Bearer",
                         "nested": [{"refresh_token": "[redacted]", "id_token": "[redacted]"}]}

    def test_jwts_are_redacted_anywhere_in_text(self):
        assert wire_capture.redact_text("seen eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.sig here") == "seen [redacted] here"

    def test_secret_query_parameters_are_redacted(self):
        assert wire_capture.redact_url(f"{BASE}/x?limit=1&token=abc") == f"{BASE}/x?limit=1&token=[redacted]"
        assert wire_capture.redact_url(f"{BASE}/x?limit=1") == f"{BASE}/x?limit=1"

    def test_a_real_call_never_exposes_the_api_key(self):
        headers = {"Content-Type": "application/json", "Set-Cookie": "sid=1"}
        client = _client({("GET", "/rest/v1/conversations"): (200, headers, {"conversations": []})})

        client.get("/rest/v1/conversations", params={"limit": 1})

        frame = wire_capture.hub.backlog[-1]
        assert SECRET_KEY.encode() not in frame and b"sid=1" not in frame
        record = json.loads(frame.split(b"data: ", 1)[1])["exchange"]
        assert record["request_headers"]["Authorization"] == "[redacted]"
        assert record["request_headers"]["Assistant-Name"] == "Demo Bot"
        assert record["url"] == f"{BASE}/rest/v1/conversations?limit=1"
        assert record["status"] == 200
        assert json.loads(record["response_body"]) == {"conversations": []}


class TestSizeCap:
    def test_large_bodies_are_truncated_with_a_marker(self):
        shown = wire_capture.format_body(b"x" * (wire_capture.MAX_BODY_BYTES + 10), "text/plain")

        assert shown.startswith("x" * 100)
        assert len(shown.encode()) < wire_capture.MAX_BODY_BYTES + 200
        assert shown.endswith(f"[truncated: showing the first 65,536 of {wire_capture.MAX_BODY_BYTES + 10:,} bytes]")

    def test_small_bodies_are_kept_whole(self):
        assert wire_capture.format_body(b"hello", "text/plain") == "hello"

    def test_a_long_event_stream_is_truncated(self):
        big = SSE * (wire_capture.MAX_BODY_BYTES // len(SSE) + 5)
        session = _session({("POST", "/stream"): (200, {"Content-Type": "text/event-stream"}, big)})

        response = session.post(f"{BASE}/stream", json={}, stream=True)
        list(response.iter_lines())
        response.close()

        body = json.loads(wire_capture.hub.backlog[-1].split(b"data: ", 1)[1])["exchange"]["response_body"]
        assert f"of {len(big):,} bytes]" in body


class TestTurnAssociation:
    def test_each_exchange_attaches_to_the_row_that_made_it(self, monkeypatch):
        client = _client({
            ("POST", "/rest/v1/conversations"): (201, {"Content-Type": "application/json"}, {"conversation_id": "conv_1"}),
            ("POST", "/rest/v1/conversations/conv_1/responses/stream"): (200, {"Content-Type": "text/event-stream"}, SSE),
            ("GET", "/rest/v1/conversations/conv_1/responses/resp_1"): (200, {"Content-Type": "application/json"}, COMPLETED),
        })
        fast_wait = functools.partial(turns.wait_for_completion, initial_delay_seconds=0, poll_interval_seconds=0)
        monkeypatch.setattr(turns, "wait_for_completion", fast_wait)

        async def collect():
            frames = turns.run_turn(client, None, "hi", None, streaming=True)
            return [f async for f in wire_capture.capture_turn(frames)]

        frames = _wire_frames(asyncio.run(collect()))

        rows = {f["id"]: f["label"] for f in frames if f.get("id")}
        attached = [(rows[f["attach"]], f["exchange"]) for f in frames if "attach" in f]
        assert [(label, ex["method"], urlsplit(ex["url"]).path) for label, ex in attached] == [
            ("POST /conversations", "POST", "/rest/v1/conversations"),
            ("POST /responses/stream", "POST", "/rest/v1/conversations/conv_1/responses/stream"),
            ("GET /responses/{id}", "GET", "/rest/v1/conversations/conv_1/responses/resp_1"),
        ]
        stream = attached[1][1]
        assert stream["streamed"] is True
        assert stream["response_body"] == SSE.decode()
        assert json.loads(stream["request_body"]) == {"input": {"chat": {"text": "hi"}}}
        assert all(ex["request_headers"]["Authorization"] == "[redacted]" for _, ex in attached)
        assert not wire_capture.hub.backlog

    def test_concurrent_turns_keep_their_own_exchanges(self):
        session = _session({
            ("GET", "/a"): (200, {}, {"turn": "a"}),
            ("GET", "/b"): (200, {}, {"turn": "b"}),
        })

        async def turn(path):
            yield wire_capture.sse_frame("wire", {"id": f"row{path}", "dir": "send", "label": path})
            for _ in range(3):
                await asyncio.to_thread(session.get, f"{BASE}{path}")
                await asyncio.sleep(0)
                yield wire_capture.sse_frame("delta", {})

        async def both():
            async def collect(path):
                return [f async for f in wire_capture.capture_turn(turn(path))]
            return await asyncio.gather(collect("/a"), collect("/b"))

        frames_a, frames_b = asyncio.run(both())

        for path, frames in (("/a", frames_a), ("/b", frames_b)):
            attached = [f for f in _wire_frames(frames) if "attach" in f]
            assert len(attached) == 3
            assert {f["attach"] for f in attached} == {f"row{path}"}
            assert {urlsplit(f["exchange"]["url"]).path for f in attached} == {path}
        assert not wire_capture.hub.backlog

    def test_calls_outside_a_turn_go_to_the_events_stream(self):
        session = _session({("GET", "/rest/v1/events"): (200, {}, {"events": []})})

        async def subscriber():
            queue: asyncio.Queue = asyncio.Queue()
            wire_capture.hub.subscribe(queue)
            try:
                await asyncio.to_thread(session.get, f"{BASE}/rest/v1/events")
                return json.loads((await asyncio.wait_for(queue.get(), 2)).split(b"data: ", 1)[1])
            finally:
                wire_capture.hub.unsubscribe(queue)

        frame = asyncio.run(subscriber())

        assert frame["background"] is True
        assert frame["label"] == "GET /rest/v1/events"
        assert frame["exchange"]["status"] == 200

    def test_a_failed_call_is_recorded_with_its_error(self):
        class Unreachable(HTTPAdapter):
            def send(self, request, **kwargs):
                raise requests.ConnectionError("connection refused")

        session = wire_capture.RecordingSession()
        session.mount("https://", Unreachable())

        with pytest.raises(requests.ConnectionError):
            session.get(f"{BASE}/x")

        record = json.loads(wire_capture.hub.backlog[-1].split(b"data: ", 1)[1])["exchange"]
        assert record["error"] == "connection refused"
        assert "status" not in record


class TestDisabled:
    def test_nothing_is_recorded_until_the_pane_turns_capture_on(self, monkeypatch):
        monkeypatch.setitem(wire_capture.state, "enabled", False)
        session = _session({("GET", "/x"): (200, {}, {})})

        async def collect():
            async def frames():
                yield wire_capture.sse_frame("wire", {"id": "w1", "dir": "send", "label": "GET /x"})
                await asyncio.to_thread(session.get, f"{BASE}/x")
            return [f async for f in wire_capture.capture_turn(frames())]

        frames = _wire_frames(asyncio.run(collect()))

        assert frames == [{"id": "w1", "dir": "send", "label": "GET /x"}]
        assert not wire_capture.hub.backlog

    def test_the_pane_turns_capture_on_and_off(self, monkeypatch):
        monkeypatch.setitem(wire_capture.state, "enabled", False)
        local = TestClient(server.app, base_url="http://127.0.0.1:8090")

        assert local.post("/api/wire", json={"capture": True}).json() == {"capture": True}
        assert wire_capture.state["enabled"] is True
        assert local.post("/api/wire", json={"capture": False}).json() == {"capture": False}
        assert wire_capture.state["enabled"] is False
