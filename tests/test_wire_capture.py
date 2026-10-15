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

import threading

import app.server as server
import app.turns as turns
from moveworks_capi.auth import ApiKeyBearer
from moveworks_capi.client import ConversationsApiClient

wire_capture = pytest.importorskip("app.wire_capture", reason="the wire capture module was deleted")

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


def _session(routes) -> "wire_capture.RecordingSession":
    session = wire_capture.RecordingSession()
    session.mount("https://", FakeApi(routes))
    wire_capture.hub.activate(session.generation)
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
    wire_capture.hub.activate(None)
    yield
    wire_capture.hub.activate(None)
    server.session.reset()


def _backlog_records() -> list:
    return [json.loads(frame.split(b"data: ", 1)[1])["exchange"] for _, frame in wire_capture.hub.backlog]


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

        shown = wire_capture.format_body(body)

        assert "grant_type=client_credentials" in shown
        assert "s3cret" not in shown and "eyJ" not in shown and "a1" not in shown
        assert shown.count("[redacted]") == 3

    def test_token_fields_in_json_bodies_are_redacted_at_any_depth(self):
        body = json.dumps({"access_token": "tok", "token_type": "Bearer", "nested": [{"refresh_token": "r", "id_token": "i"}]})

        shown = json.loads(wire_capture.format_body(body.encode()))

        assert shown == {"access_token": "[redacted]", "token_type": "Bearer",
                         "nested": [{"refresh_token": "[redacted]", "id_token": "[redacted]"}]}

    def test_jwts_are_redacted_anywhere_in_text(self):
        assert wire_capture.redact_text("seen eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.sig here") == "seen [redacted] here"

    def test_secret_query_parameters_are_redacted(self):
        assert wire_capture.redact_url(f"{BASE}/x?limit=1&token=abc") == f"{BASE}/x?limit=1&token=[redacted]"
        assert wire_capture.redact_url(f"{BASE}/x?limit=1") == f"{BASE}/x?limit=1"

    def test_credential_names_match_in_any_case_or_separator_style(self):
        body = json.dumps({"accessToken": "a", "clientSecret": "b", "refreshToken": "c", "x-api-key": "d",
                           "userPassword": "e", "error": {"code": "INVALID_JWT", "message": "bad"}})

        shown = json.loads(wire_capture.format_body(body))

        assert shown == {"accessToken": "[redacted]", "clientSecret": "[redacted]", "refreshToken": "[redacted]",
                         "x-api-key": "[redacted]", "userPassword": "[redacted]",
                         "error": {"code": "INVALID_JWT", "message": "bad"}}

    def test_oauth_code_and_verifier_are_redacted_in_form_bodies(self):
        shown = wire_capture.format_body(b"grant_type=authorization_code&code=abc123&code_verifier=v3r1f13r&redirect_uri=x")

        assert "abc123" not in shown and "v3r1f13r" not in shown
        assert "grant_type=authorization_code" in shown and "redirect_uri=x" in shown

    def test_form_body_without_a_content_type_is_still_redacted(self):
        request = requests.Request("POST", f"{BASE}/token", data="client_secret=s3cret&scope=all").prepare()
        assert "Content-Type" not in request.headers

        record = wire_capture.exchange(request, None, 0.0)

        assert "s3cret" not in record["request_body"]
        assert "scope=all" in record["request_body"]

    def test_json_inside_a_string_field_is_redacted(self):
        body = json.dumps({"payload": json.dumps({"token": "t0k", "email": "user@example.com"})})

        shown = json.loads(wire_capture.format_body(body))

        assert json.loads(shown["payload"]) == {"token": "[redacted]", "email": "user@example.com"}

    def test_jwts_in_the_path_and_query_are_redacted(self):
        jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"

        shown = wire_capture.redact_url(f"{BASE}/session/{jwt}/x?jwt={jwt}&limit=1")

        assert jwt not in shown
        assert shown == f"{BASE}/session/[redacted]/x?jwt=[redacted]&limit=1"

    def test_presigned_url_signatures_are_redacted(self):
        url = f"{BASE}/f?X-Amz-Signature=abc&X-Amz-Security-Token=def&X-Amz-Expires=60&sig=ghi"

        assert wire_capture.redact_url(url) == (
            f"{BASE}/f?X-Amz-Signature=[redacted]&X-Amz-Security-Token=[redacted]&X-Amz-Expires=60&sig=[redacted]"
        )

    def test_secrets_inside_header_values_are_redacted(self):
        shown = wire_capture.redact_headers({"Location": "https://example.com/cb?token=abc&state=1"})

        assert shown == {"Location": "https://example.com/cb?token=[redacted]&state=1"}

    def test_error_strings_are_redacted(self):
        request = requests.Request("GET", f"{BASE}/x").prepare()

        record = wire_capture.exchange(
            request, None, 0.0, error=f"HTTPSConnectionPool: Max retries exceeded with url: /x?api_key={SECRET_KEY} (Bearer {SECRET_KEY})",
        )

        assert SECRET_KEY not in record["error"]
        assert "api_key=[redacted]" in record["error"] and "Bearer [redacted]" in record["error"]

    def test_a_real_call_never_exposes_the_api_key(self):
        headers = {"Content-Type": "application/json", "Set-Cookie": "sid=1"}
        client = _client({("GET", "/rest/v1/conversations"): (200, headers, {"conversations": []})})

        client.get("/rest/v1/conversations", params={"limit": 1})

        frame = wire_capture.hub.backlog[-1][1]
        assert SECRET_KEY.encode() not in frame and b"sid=1" not in frame
        record = json.loads(frame.split(b"data: ", 1)[1])["exchange"]
        assert record["request_headers"]["Authorization"] == "[redacted]"
        assert record["request_headers"]["Assistant-Name"] == "Demo Bot"
        assert record["url"] == f"{BASE}/rest/v1/conversations?limit=1"
        assert record["status"] == 200
        assert json.loads(record["response_body"]) == {"conversations": []}


class TestSizeCap:
    def test_large_bodies_are_truncated_with_a_marker(self):
        shown = wire_capture.format_body(b"x" * (wire_capture.MAX_BODY_BYTES + 10))

        assert shown.startswith("x" * 100)
        assert len(shown.encode()) < wire_capture.MAX_BODY_BYTES + 200
        assert shown.endswith(f"[truncated: showing the first 65,536 of {wire_capture.MAX_BODY_BYTES + 10:,} bytes]")

    def test_small_bodies_are_kept_whole(self):
        assert wire_capture.format_body(b"hello") == "hello"

    def test_a_long_event_stream_is_truncated(self):
        big = SSE * (wire_capture.MAX_BODY_BYTES // len(SSE) + 5)
        session = _session({("POST", "/stream"): (200, {"Content-Type": "text/event-stream"}, big)})

        response = session.post(f"{BASE}/stream", json={}, stream=True)
        list(response.iter_lines())
        response.close()

        body = _backlog_records()[-1]["response_body"]
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
        wire_capture.hub.activate(session.generation)

        with pytest.raises(requests.ConnectionError):
            session.get(f"{BASE}/x")

        record = _backlog_records()[-1]
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


class TestConnections:
    def test_a_poll_in_flight_from_the_previous_connection_never_reaches_the_next(self, monkeypatch):
        release = threading.Event()

        class SlowPollFromA(HTTPAdapter):
            def send(self, request, **kwargs):
                release.wait(5)
                return FakeApi({("GET", urlsplit(request.url).path): (200, {}, {"events": [{"text": "A-private-event"}]})}).send(request)

        session_a = wire_capture.RecordingSession()
        session_a.mount("https://", SlowPollFromA())
        wire_capture.hub.activate(session_a.generation)
        poll_a = threading.Thread(target=session_a.get, args=(f"{BASE}/rest/v1/conversations/-/events",))
        poll_a.start()

        class SessionForB(wire_capture.RecordingSession):
            def __init__(self):
                super().__init__()
                self.mount("https://", FakeApi(DefaultRoutes({("GET", "/rest/v1/conversations"): (200, {}, {"conversations": []})})))

        monkeypatch.setattr(wire_capture, "RecordingSession", SessionForB)
        local = TestClient(server.app, base_url="http://127.0.0.1:8090")
        assert local.post("/api/connect", json={"base_url": BASE, "bot_name": "Bot B", "api_key": "key-b"}).status_code == 200
        release.set()
        poll_a.join(5)

        records = _backlog_records()
        assert records and all(r["request_headers"]["Assistant-Name"] == "Bot B" for r in records)
        assert not any(b"A-private-event" in frame for _, frame in wire_capture.hub.backlog)

    def test_capture_stops_when_the_last_events_stream_closes(self):
        async def open_and_close():
            first, second = asyncio.Queue(), asyncio.Queue()
            wire_capture.hub.subscribe(first)
            wire_capture.hub.subscribe(second)
            wire_capture.hub.unsubscribe(first)
            still_on = wire_capture.state["enabled"]
            wire_capture.hub.unsubscribe(second)
            return still_on

        assert asyncio.run(open_and_close()) is True
        assert wire_capture.state["enabled"] is False

    def test_message_endpoint_attaches_exchanges_to_the_turn_rows(self, monkeypatch):
        server.session.client = _client({
            ("POST", "/rest/v1/conversations"): (201, {"Content-Type": "application/json"}, {"conversation_id": "conv_1"}),
            ("POST", "/rest/v1/conversations/conv_1/responses/stream"): (200, {"Content-Type": "text/event-stream"}, SSE),
            ("GET", "/rest/v1/conversations/conv_1/responses/resp_1"): (200, {"Content-Type": "application/json"}, COMPLETED),
        })
        fast_wait = functools.partial(turns.wait_for_completion, initial_delay_seconds=0, poll_interval_seconds=0)
        monkeypatch.setattr(turns, "wait_for_completion", fast_wait)
        monkeypatch.setitem(server.settings, "streaming", True)
        local = TestClient(server.app, base_url="http://127.0.0.1:8090")

        body = local.post("/api/message", json={"text": "hi"}).text

        frames = _wire_frames([chunk.encode() + b"\n\n" for chunk in body.strip().split("\n\n")])
        rows = {f["id"]: f["label"] for f in frames if f.get("id")}
        assert [rows[f["attach"]] for f in frames if "attach" in f] == [
            "POST /conversations", "POST /responses/stream", "GET /responses/{id}",
        ]
        assert SECRET_KEY not in body


class DefaultRoutes(dict):
    """Routes with an empty 200 for anything not listed, such as event polls."""

    def __missing__(self, key):
        return (200, {"Content-Type": "application/json"}, {})
