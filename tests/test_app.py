"""Demo app server: security rules, message presentation, turns."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.present as present
import app.server as server
import app.turns as turns
from moveworks_capi.errors import RequestRejectedError, StreamClosedError, StreamError
from moveworks_capi.responses import Response
from moveworks_capi.streaming import ResponseCreated
from tests.conftest import PORTAL_REDIRECTS

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def local():
    return TestClient(server.app, base_url="http://127.0.0.1:8090")


@pytest.fixture(autouse=True)
def fresh_session():
    server.session.reset()
    yield
    server.session.reset()


class TestSecurity:
    def test_local_requests_are_served(self, local):
        assert local.get("/api/session").status_code == 200

    def test_foreign_host_header_is_refused(self):
        # DNS rebinding: a hostile name resolving to 127.0.0.1.
        rebound = TestClient(server.app, base_url="http://attacker.example:8090")
        assert rebound.get("/api/session").status_code == 400

    def test_cross_origin_post_is_refused(self, local):
        resp = local.post("/api/disconnect", json={}, headers={"Origin": "https://attacker.example"})
        assert resp.status_code == 403

    def test_non_json_post_is_refused(self, local):
        # A plain form or text/plain POST needs no CORS preflight.
        resp = local.post("/api/disconnect", content="{}", headers={"Content-Type": "text/plain"})
        assert resp.status_code == 415

    def test_same_origin_json_post_is_served(self, local):
        resp = local.post("/api/disconnect", json={}, headers={"Origin": "http://127.0.0.1:8090"})
        assert resp.status_code == 200


class TestCaching:
    def test_page_and_static_files_revalidate(self, local):
        for path in ("/", "/static/js/chat.js", "/static/styles.css"):
            assert local.get(path).headers["cache-control"] == "no-cache", path

    def test_api_responses_keep_their_own_headers(self, local):
        assert "cache-control" not in local.get("/api/session").headers


def _form_message() -> dict:
    intro = "I also found a **Corp VPN** form ."
    return {
        "message_id": "msg_1",
        "response_id": "resp_1",
        "actor": "ASSISTANT",
        "content": {"type": "MARKDOWN_TEXT", "markdown_text": {
            "text": f"{intro}\n\n👉 **Which device?**\n\n**Corp VPN**\nRemote access to Internal Corporate Systems",
        }},
        "citations": [
            {"citation_id": "cite_form", "url": "https://example.service-now.com/form", "display": {
                "title": {"markdown_text": {"text": "**<https://example.service-now.com/form|Corp VPN>**"}},
                "body": {"markdown_text": {"text": "Remote access to Internal Corporate Systems\nMore."}},
            }},
            {"citation_id": "cite_kb", "url": "https://example.service-now.com/kb", "display": {
                "title": {"markdown_text": {"text": "<https://example.service-now.com/kb|KB0001>: VPN setup"}},
                "body": {"markdown_text": {"text": "Open <https://example.com|settings> :point_right: now"}},
                "attributes": [{"key": "TICKET_STATE", "value": "NEW"}],
            }},
        ],
        "content_references": [{"citation_id": "cite_form", "offset": len(intro)}],
        "actions": [],
    }


class TestSession:
    def test_session_lists_the_data_center_base_urls(self, local):
        urls = local.get("/api/session").json()["known_base_urls"]
        assert urls[0] == "https://api.moveworks.ai"
        assert "https://api.am-eu-central.moveworks.ai" in urls
        assert all(url.startswith("https://api.") for url in urls)


class TestPresentMessage:
    def test_smart_handoff_payload(self):
        message = json.loads((FIXTURES / "smart_handoff_message.json").read_text())

        shown = present.present_message(message)

        assert [(a["label"], a["type"]) for a in shown["actions"]] == [
            ("File ticket", "CALLBACK_ACTION"),
            ("Edit/Add details", "MODAL_ACTION"),
            ("Other help options", "URL_ACTION"),
        ]
        # Smart handoff rides on actions; Message.handoff stays empty.
        assert shown["handoff"] == []
        assert shown["feedback"] == {"helpful": "callback_fixture", "unhelpful": "callback_fixture"}
        assert shown["text"].startswith("I understand you’d like to file a ticket")

    def test_user_messages_carry_plain_text(self):
        user = {"actor": "USER", "content": {"type": "PLAIN_TEXT", "plain_text": {"text": "I need a laptop"}}}
        assert present.present_message(user)["text"] == "I need a laptop"

    def test_user_attachments_are_listed_by_name(self):
        user = {"actor": "USER", "content": {"type": "PLAIN_TEXT", "plain_text": {
            "text": "see attached", "files": [{"file_id": "file_a", "file_name": "vpn-error.png"}, {"file_id": "file_b"}]}}}
        shown = present.present_message(user)
        assert (shown["text"], shown["files"], shown["is_action"]) == ("see attached", ["vpn-error.png", "file_b"], False)

    def test_button_presses_in_history_are_flagged(self):
        user = {"actor": "USER", "content": {"type": "CALLBACK_ACTION_CONTENT", "callback_action": {"callback_id": "cb"}}}
        shown = present.present_message(user)
        assert (shown["text"], shown["is_action"]) == ("", True)

    def test_inline_forms_split_the_answer_and_drop_the_form_from_sources(self):
        shown = present.present_message(_form_message())

        assert shown["layout"] == [
            {"text": "I also found a **Corp VPN** form.", "forms": ["Corp VPN"]},
            {"text": "👉 **Which device?**", "forms": []},
        ]
        assert [s["title"] for s in shown["sources"]] == ["KB0001: VPN setup"]
        assert shown["sources"][0]["snippet"] == "Open [settings](https://example.com) 👉 now"
        assert shown["sources"][0]["state"] == "NEW"

    def test_without_inline_forms_the_text_renders_as_sent(self, monkeypatch):
        monkeypatch.setattr(present, "answer_layout", None)
        monkeypatch.setattr(present, "form_citation_ids", None)

        shown = present.present_message(_form_message())

        assert shown["layout"] is None
        assert shown["text"].endswith("Remote access to Internal Corporate Systems")
        assert [s["title"] for s in shown["sources"]] == ["Corp VPN", "KB0001: VPN setup"]

    def test_portal_form_links_carry_the_form_they_open(self, offline_short_links):
        offline_short_links.update(PORTAL_REDIRECTS)
        shown = present.present_message(json.loads((FIXTURES / "portal_form_message.json").read_text()))

        assert [a["form"]["title"] for a in shown["actions"]] == [
            "401(k) Retirement Plan Inquiry",
            "401(k) Plan Enrollment Inquiry",
            "401(k) Plan Enrollment",
        ]
        assert shown["actions"][0]["form"]["description"] == "Ask a question about your existing plan"
        assert [s["forms"] for s in shown["layout"]][0] == [
            "401(k) Plan Enrollment", "401(k) Plan Enrollment Inquiry", "401(k) Retirement Plan Inquiry",
        ]

    def test_streamed_snapshots_skip_short_link_lookups(self, offline_short_links):
        offline_short_links.update(PORTAL_REDIRECTS)
        message = json.loads((FIXTURES / "portal_form_message.json").read_text())

        shown = present.present_message(message, resolve_links=False)

        assert not any("form" in a for a in shown["actions"])

    def test_live_agent_messages_carry_the_agent_identity(self):
        # Shape per the API reference.
        message = {
            "message_id": "msg_agent", "actor": "LIVE_AGENT",
            "content": {"type": "MARKDOWN_TEXT", "markdown_text": {"text": "Hi, I'm looking at your VPN issue now."}},
            "sender_info": {"display_name": "Dana Fielding", "avatar_url": "https://example.com/dana.png"},
        }

        shown = present.present_message(message)

        assert shown["actor"] == "LIVE_AGENT"
        assert shown["sender"] == {"display_name": "Dana Fielding", "avatar_url": "https://example.com/dana.png"}
        assert present.present_message({**message, "sender_info": {"display_name": "Dana"}})["sender"]["avatar_url"] == ""
        assert present.present_message({"actor": "ASSISTANT"})["sender"] is None

    def test_get_help_menu_from_message_handoff(self):
        # Shape per the API reference.
        message = {"handoff": {"handoff_categories": [{
            "category_id": "it", "name": ":computer: IT", "description": "Hardware and access",
            "handoff_items": [
                {"title": "File a ticket", "type": "ACTION", "action": {
                    "label": "File a ticket", "type": "MODAL_ACTION", "modal_action": {"modal_id": "modal_1"}}},
                {"title": "Hours", "type": "TEXT", "text": {"type": "MARKDOWN_TEXT", "markdown_text": {"text": "See <https://example.com|hours>"}}},
            ],
        }]}}

        menu = present.present_message(message)["handoff"]

        assert menu[0]["name"] == "IT"
        assert menu[0]["items"][0]["action"]["modal_id"] == "modal_1"
        assert menu[0]["items"][1]["text"] == "See [hours](https://example.com)"

    def test_modal_display_html_becomes_markdown(self):
        modal = {"title": ":memo: New request", "fields": [
            {"type": "DISPLAY", "display": {"text": {"style": "SUBTLE", "text": 'Having issues? <a href="https://example.com">Portal</a>'}}},
            {"type": "TEXT", "name": "summary", "text": {}},
        ]}

        shown = present.present_modal(modal)

        assert shown["title"] == "📝 New request"
        assert shown["fields"][0]["display"]["text"]["text"] == "Having issues? [Portal](https://example.com)"
        assert shown["fields"][1] == modal["fields"][1]


class TestHistory:
    def test_messages_are_presented_oldest_first(self, local, monkeypatch):
        server.session.client = object()
        newest_first = {"messages": [
            {"message_id": "m2", "actor": "ASSISTANT", "content": {"type": "MARKDOWN_TEXT", "markdown_text": {"text": "Hi!"}}},
            {"message_id": "m1", "actor": "USER", "content": {"type": "PLAIN_TEXT", "plain_text": {"text": "hello"}}},
        ]}
        monkeypatch.setattr(server, "list_messages", lambda client, cid, limit, cursor: newest_first)

        body = local.get("/api/conversations/conv_1/messages").json()

        assert [(m["message_id"], m["actor"], m["text"]) for m in body["messages"]] == [
            ("m1", "USER", "hello"),
            ("m2", "ASSISTANT", "Hi!"),
        ]
        assert body["next_cursor"] is None

    def test_older_pages_pass_the_cursor_through(self, local, monkeypatch):
        server.session.client = object()
        seen = {}

        def fake(client, cid, limit, cursor):
            seen.update(limit=limit, cursor=cursor)
            return {"messages": [], "metadata": {"next_cursor": "cur_2"}}

        monkeypatch.setattr(server, "list_messages", fake)

        body = local.get("/api/conversations/conv_1/messages?cursor=cur_1").json()

        assert seen == {"limit": 50, "cursor": "cur_1"}
        assert body["next_cursor"] == "cur_2"


class TestConversationsSidebar:
    def test_archived_view_and_cursor_reach_the_api(self, local, monkeypatch):
        server.session.client = object()
        seen = {}
        monkeypatch.setattr(server, "list_conversations", lambda client, **kw: seen.update(kw) or {"conversations": []})

        local.get("/api/conversations?limit=20&archived=true&cursor=abc")

        assert seen == {"limit": 20, "cursor": "abc", "archived": True}

    def test_rename_and_archive_patch_the_conversation(self, local, monkeypatch):
        server.session.client = object()
        calls = []
        monkeypatch.setattr(server, "update_conversation", lambda client, cid, **kw: calls.append((cid, kw)) or {"conversation_id": cid})

        assert local.patch("/api/conversations/conv_1", json={"title": "  VPN trouble "}).status_code == 200
        assert local.patch("/api/conversations/conv_1", json={"archived": True}).status_code == 200

        assert calls == [
            ("conv_1", {"title": "VPN trouble", "archived": None}),
            ("conv_1", {"title": None, "archived": True}),
        ]

    def test_empty_patch_and_blank_title_are_rejected(self, local):
        server.session.client = object()
        assert local.patch("/api/conversations/conv_1", json={}).status_code == 400
        assert local.patch("/api/conversations/conv_1", json={"title": "   "}).status_code == 400

    def test_cross_origin_patch_is_refused(self, local):
        resp = local.patch("/api/conversations/conv_1", json={"archived": True}, headers={"Origin": "https://attacker.example"})
        assert resp.status_code == 403


class TestFeedback:
    def test_comment_rides_along_as_additional_feedback(self, local):
        sent = []

        class Client:
            def post(self, path, json):
                sent.append(json)
                return type("R", (), {"json": lambda self: {"status": "OK"}})()

        server.session.client = Client()
        ref = {"conversation_id": "c", "response_id": "r", "message_id": "m", "callback_id": "cb"}

        local.post("/api/feedback", json={**ref, "additional_feedback": "  wrong form  "})
        local.post("/api/feedback", json={**ref, "additional_feedback": "   "})
        local.post("/api/feedback", json=ref)

        assert sent == [
            {"callback_id": "cb", "additional_feedback": "wrong form"},
            {"callback_id": "cb"},
            {"callback_id": "cb"},
        ]


class TestModalRefresh:
    def test_refresh_sends_every_value_and_presents_the_result(self, local):
        sent = []

        class Client:
            def post(self, path, json):
                sent.append((path, json))
                modal = {"modal_id": "modal_1", "title": "New laptop", "fields": [
                    {"name": "intro", "type": "DISPLAY", "display": {"text": {"text": "Pick one<br>then submit"}}}]}
                return type("R", (), {"json": lambda self: modal})()

        server.session.client = Client()
        ref = {"conversation_id": "c", "response_id": "r", "message_id": "m", "modal_id": "modal_1"}
        values = {"model": {"option": "mac"}, "reason": {"text": "broken"}}

        shown = local.post("/api/modal/refresh", json={**ref, "values": values}).json()

        assert sent == [("/rest/v1/conversations/c/responses/r/messages/m/modals/modal_1/refresh", {"values": values})]
        assert shown["fields"][0]["display"]["text"]["text"] == "Pick one\nthen submit"

    def test_missing_modal_explains_fresh_ids_and_availability(self, local):
        class Client:
            def post(self, path, json):
                raise RequestRejectedError("request rejected (404): 404 not found", status_code=404)

        server.session.client = Client()
        ref = {"conversation_id": "c", "response_id": "r", "message_id": "m", "modal_id": "modal_1"}
        detail = local.post("/api/modal/refresh", json={**ref, "values": {}}).json()["detail"]
        assert "re-read the message" in detail and "modals are available" in detail


class _FakeClient:
    def __init__(self):
        self.posts = []

    def post(self, path, json=None):
        self.posts.append((path, json))
        payload = {"conversation_id": "conv_new"} if path.endswith("/conversations") else {"response_id": "resp_1"}
        return type("R", (), {"json": lambda self: payload})()


def _frames(raw: list) -> list:
    out = []
    for chunk in raw:
        event, data = chunk.decode().strip().split("\n", 1)
        out.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return [f for f in out if f[0] != "wire"]


class TestTurns:
    def test_submit_and_poll_turn_yields_the_same_frames_as_streaming(self, monkeypatch):
        message = json.loads((FIXTURES / "smart_handoff_message.json").read_text())
        completed = Response(
            response_id="resp_1", conversation_id="conv_new", status="COMPLETED",
            outputs=[{"type": "MESSAGE", "message": message}], raw={},
        )
        monkeypatch.setattr(turns, "wait_for_completion", lambda client, cid, rid, **kw: completed)
        client = _FakeClient()

        async def collect():
            return [f async for f in turns.run_turn(client, None, "file a ticket", None, streaming=False)]

        frames = _frames(asyncio.run(collect()))

        assert [event for event, _ in frames] == ["conversation", "delta", "completed", "actions", "feedback_ids"]
        assert client.posts[0] == ("/rest/v1/conversations", {"title": "file a ticket"})
        assert client.posts[1] == ("/rest/v1/conversations/conv_new/responses", {"input": {"chat": {"text": "file a ticket"}}})
        assert frames[1][1]["output_type"] == "MESSAGE"
        assert len(frames[3][1]["actions"]) == 3


    def test_empty_callback_response_surfaces_the_reply_filed_under_an_earlier_response(self, monkeypatch):
        # Exit Live Chat completes with no outputs; the farewell sits in history under
        # the handoff turn's response and is not an event.
        empty = Response(response_id="resp_exit", conversation_id="conv_1", status="COMPLETED", outputs=[], raw={})
        monkeypatch.setattr(turns, "wait_for_completion", lambda client, cid, rid, **kw: empty)
        history = {"messages": [
            {"message_id": "m3", "actor": "ASSISTANT", "response_id": "resp_handoff", "created_at": "2026-09-25T21:28:54Z",
             "content": {"type": "MARKDOWN_TEXT", "markdown_text": {"text": "You've ended your live agent chat session."}}},
            {"message_id": "m2", "actor": "USER", "response_id": "resp_exit", "created_at": "2026-09-25T21:28:53Z",
             "content": {"type": "CALLBACK_ACTION_CONTENT"}},
            {"message_id": "m1", "actor": "LIVE_AGENT", "response_id": "resp_agent", "created_at": "2026-09-25T21:28:11Z",
             "content": {"type": "MARKDOWN_TEXT", "markdown_text": {"text": "let me look into it"}}},
        ]}
        monkeypatch.setattr(turns, "list_messages", lambda client, cid, limit: history)

        async def collect():
            return [f async for f in turns.run_turn(_FakeClient(), "conv_1", None, "cb_exit", streaming=False)]

        frames = _frames(asyncio.run(collect()))

        late = [data for event, data in frames if event == "late_message"]
        assert [m["message"]["text"] for m in late] == ["You've ended your live agent chat session."]


    def test_stream_that_closes_before_the_answer_falls_back_to_polling(self, monkeypatch):
        # The stream closes after RESPONSE_IN_PROGRESS and the answer lands after a
        # short poll would have given up.
        message = {"message_id": "m1", "actor": "ASSISTANT",
                   "content": {"type": "MARKDOWN_TEXT", "markdown_text": {"text": "Here is how to fix your VPN."}}}
        done = Response(response_id="resp_1", conversation_id="conv_1", status="COMPLETED",
                        outputs=[{"type": "MESSAGE", "message": message}], raw={})
        timeouts = []

        async def cut_short_stream(client, cid, text, callback_id, on_response_id, on_answer, on_completed):
            on_response_id("resp_1")
            yield b"event: wire\ndata: {}\n\n"

        def fake_wait(client, cid, rid, timeout_seconds=60.0, **kw):
            timeouts.append(timeout_seconds)
            return done

        monkeypatch.setattr(turns, "stream_turn", cut_short_stream)
        monkeypatch.setattr(turns, "wait_for_completion", fake_wait)

        async def collect():
            return [f async for f in turns.run_turn(_FakeClient(), "conv_1", "vpn help", None, streaming=True)]

        frames = _frames(asyncio.run(collect()))

        assert timeouts == [turns.STREAM_FALLBACK_TIMEOUT_SECONDS]
        deltas = [d for e, d in frames if e == "delta"]
        assert deltas and deltas[0]["text"] == "Here is how to fix your VPN."
        assert ("completed", {"status": "COMPLETED"}) in frames

    def test_stream_closed_error_after_the_response_id_falls_back_to_polling(self, monkeypatch):
        # The library raises StreamClosedError when the stream ends before a terminal
        # event; run the real app.streaming.stream_turn over a stream that does that.
        import app.streaming as streaming

        message = {"message_id": "m1", "actor": "ASSISTANT",
                   "content": {"type": "MARKDOWN_TEXT", "markdown_text": {"text": "Here is how to fix your VPN."}}}
        done = Response(response_id="resp_1", conversation_id="conv_1", status="COMPLETED",
                        outputs=[{"type": "MESSAGE", "message": message}], raw={})
        timeouts = []

        def closed_early(*a, **kw):
            yield ResponseCreated(sequence_number=0, raw={}, response_id="resp_1", conversation_id="conv_1")
            raise StreamClosedError("stream closed before a terminal event", response_id="resp_1")

        def fake_wait(client, cid, rid, timeout_seconds=60.0, **kw):
            timeouts.append((rid, timeout_seconds))
            return done

        monkeypatch.setattr(streaming, "stream_response", closed_early)
        monkeypatch.setattr(turns, "stream_turn", streaming.stream_turn)
        monkeypatch.setattr(turns, "wait_for_completion", fake_wait)

        async def collect():
            return [f async for f in turns.run_turn(_FakeClient(), "conv_1", "vpn help", None, streaming=True)]

        frames = _frames(asyncio.run(collect()))

        assert timeouts == [("resp_1", turns.STREAM_FALLBACK_TIMEOUT_SECONDS)]
        deltas = [d for e, d in frames if e == "delta"]
        assert deltas and deltas[0]["text"] == "Here is how to fix your VPN."
        assert ("completed", {"status": "COMPLETED"}) in frames
        assert not [d for e, d in frames if e == "error"]

    def test_stream_closed_before_any_response_id_is_reported(self, monkeypatch):
        polled = []

        async def closed_stream(client, cid, text, callback_id, on_response_id, on_answer, on_completed):
            raise StreamClosedError("stream closed before a terminal event")
            yield b""

        monkeypatch.setattr(turns, "stream_turn", closed_stream)
        monkeypatch.setattr(turns, "wait_for_completion", lambda *a, **kw: polled.append(a))

        async def collect():
            return [f async for f in turns.run_turn(_FakeClient(), "conv_1", "hi", None, streaming=True)]

        frames = _frames(asyncio.run(collect()))

        assert frames == [("error", {"message": "stream closed before a terminal event"})]
        assert polled == []

    def test_stream_error_event_is_reported_without_polling(self, monkeypatch):
        polled = []

        async def failing_stream(client, cid, text, callback_id, on_response_id, on_answer, on_completed):
            on_response_id("resp_1")
            yield b"event: wire\ndata: {}\n\n"
            raise StreamError("Something went wrong generating the answer", code="INTERNAL")

        monkeypatch.setattr(turns, "stream_turn", failing_stream)
        monkeypatch.setattr(turns, "wait_for_completion", lambda *a, **kw: polled.append(a))

        async def collect():
            return [f async for f in turns.run_turn(_FakeClient(), "conv_1", "vpn help", None, streaming=True)]

        frames = _frames(asyncio.run(collect()))

        assert frames == [("error", {"message": "Something went wrong generating the answer"})]
        assert polled == []

    def test_poll_timeout_is_reported_instead_of_ending_silently(self, monkeypatch):
        async def cut_short_stream(client, cid, text, callback_id, on_response_id, on_answer, on_completed):
            on_response_id("resp_1")
            yield b"event: wire\ndata: {}\n\n"

        def fake_wait(*a, **kw):
            raise TimeoutError("still IN_PROGRESS")

        monkeypatch.setattr(turns, "stream_turn", cut_short_stream)
        monkeypatch.setattr(turns, "wait_for_completion", fake_wait)

        async def collect():
            return [f async for f in turns.run_turn(_FakeClient(), "conv_1", "vpn help", None, streaming=True)]

        frames = _frames(asyncio.run(collect()))

        assert [e for e, _ in frames] == ["error"]


class TestNotifications:
    def test_backlog_from_the_first_poll_is_not_dispatched(self):
        queue: asyncio.Queue = asyncio.Queue()
        server.session.event_subscribers.append(queue)
        event = object()

        server._dispatch_polled_event("app-user", event)
        assert queue.empty()

        server.session.catching_up = False
        server._dispatch_polled_event("app-user", event)
        assert queue.get_nowait() is event
