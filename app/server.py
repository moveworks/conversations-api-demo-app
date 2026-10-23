"""Moveworks Conversations API: demo app.

Connect to a Moveworks instance by pasting its base URL, Bot Name, and an API key,
then chat: replies with the reasoning trail, action buttons (including
OBO/UCA consent links), native modal forms, Get Help menus, citations,
feedback, and proactive notifications.

The key is held in this process's memory only: never written to disk, never
logged. The server proxies every Conversations API call (browsers cannot call
the API cross-origin, and the key should not live in browser storage).

Run::

    python -m app              # then open http://127.0.0.1:8090
    python -m app --no-stream  # submit and poll instead of streaming

The app runs locally: one connected Moveworks instance per process, bound to
the loopback interface, with no login. Do not expose it on a network.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import io
import logging
import socket
import sys
import time
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from moveworks_capi.auth import ApiKeyBearer
from moveworks_capi.client import ConversationsApiClient
from moveworks_capi.conversations import list_conversations, list_messages, update_conversation
from moveworks_capi.errors import AuthenticationError, RateLimitedError, RequestRejectedError
from moveworks_capi.notifications import Event, EventsPollingClient, InMemoryCheckpoint, PollingLoop
from moveworks_capi.notifications.polling import event_sort_key

from app.frames import sse_frame
from app.present import present_message, present_modal, present_notification_text
from app.turns import run_turn

try:  # Optional: delete app/wire_capture.py (with js/optional/wire.js) to drop the expandable wire rows.
    from app import wire_capture
except ImportError:  # pragma: no cover - exercised by deleting the module
    wire_capture = None

try:  # Optional: delete app/attachments.py (with js/optional/attachments.js) to drop file attachments.
    from app.attachments import register as register_attachments
except ImportError:  # pragma: no cover - exercised by deleting the module
    register_attachments = None

try:  # Optional: delete app/test_notifications.py to drop the "send a test notification" panel.
    from app.test_notifications import register as register_test_notifications
except ImportError:  # pragma: no cover - exercised by deleting the module
    register_test_notifications = None

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Without a Cache-Control header browsers guess freshness from Last-Modified
# and keep running old JS after an edit; revalidating costs only a 304.
REVALIDATE = {"Cache-Control": "no-cache"}


class RevalidatedStaticFiles(StaticFiles):
    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.update(REVALIDATE)
        return response
HOST = "127.0.0.1"
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost"}

KNOWN_BASE_URLS = [
    "https://api.moveworks.ai",
    "https://api.am-ca-central.moveworks.ai",
    "https://api.am-eu-central.moveworks.ai",
    "https://api.am-ap-southeast.moveworks.ai",
    "https://api.moveworksgov.ai",
    "https://api.jp.moveworks.com",
    "https://api.uk.moveworks.com",
    "https://api.prod4.us.moveworks.com",
    "https://api.prod3.us.moveworks.com",
]

# Event polls count against the same rate limit as history and list reads, so a
# faster poll starves the sidebar.
POLL_INTERVAL_SECONDS = 6.0
# Events created this soon before connect still count as new, to absorb clock
# skew between this machine and Moveworks.
CONNECT_CLOCK_SKEW_SECONDS = 5.0
POLL_USER = "app-user"


class Session:
    """One connected Moveworks instance. Lives in process memory only."""

    def __init__(self) -> None:
        self.client: Optional[ConversationsApiClient] = None
        self.base_url: str = ""
        self.bot_name: str = ""
        self.polling_task: Optional[asyncio.Task] = None
        self.polling_loop: Optional[PollingLoop] = None
        self.event_subscribers: list[asyncio.Queue] = []
        # Proactive notifications route to the user's most recent channel, so
        # a test notification is only meaningful after a message from here.
        self.last_message_at: Optional[float] = None
        self.checkpoint = InMemoryCheckpoint()
        self.connected_at: float = 0.0
        # Polls run in a worker thread; events reach the SSE queues through this loop.
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def connected(self) -> bool:
        return self.client is not None

    def reset(self) -> None:
        if self.polling_task:
            self.polling_task.cancel()
        self.__init__()


session = Session()
settings = {"streaming": True}


class ConnectIn(BaseModel):
    base_url: str
    bot_name: str
    api_key: str


class MessageIn(BaseModel):
    text: Optional[str] = None
    callback_id: Optional[str] = None
    conversation_id: Optional[str] = None
    # Upload ids from app.attachments; the API takes at most five per turn.
    file_ids: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list, max_length=5)


class ModalRef(BaseModel):
    conversation_id: str
    response_id: str
    message_id: str
    modal_id: str


class ModalSubmitIn(ModalRef):
    values: dict


class ModalRefreshIn(ModalRef):
    values: dict


class FeedbackIn(BaseModel):
    conversation_id: str
    response_id: str
    message_id: str
    callback_id: str
    additional_feedback: Optional[str] = None


class ConversationPatchIn(BaseModel):
    title: Optional[str] = None
    archived: Optional[bool] = None


def build_app() -> FastAPI:
    app = FastAPI(title="Moveworks Conversations API: demo app")

    # A page on another site can reach 127.0.0.1 through DNS rebinding or a
    # simple cross-site POST. Both would act with the connected API key, so
    # accept only local Host headers, and only JSON writes from this origin.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(LOCAL_HOSTNAMES))

    @app.middleware("http")
    async def same_origin_json_writes(request: Request, call_next):
        if request.method in ("POST", "PATCH", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
                return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
            if not (request.headers.get("content-type", "").startswith("application/json") or _is_raw_upload(request)):
                return JSONResponse({"detail": "Send JSON."}, status_code=415)
        return await call_next(request)

    app.mount("/static", RevalidatedStaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=REVALIDATE)

    @app.get("/api/session")
    async def get_session() -> dict:
        return {
            "connected": session.connected,
            "base_url": session.base_url,
            "bot_name": session.bot_name,
            "known_base_urls": KNOWN_BASE_URLS,
        }

    @app.post("/api/connect")
    async def connect(body: ConnectIn) -> dict:
        base_url = body.base_url.strip().rstrip("/")
        bot_name = body.bot_name.strip()
        api_key = body.api_key.strip()
        if not (base_url.startswith("https://") and bot_name and api_key):
            raise HTTPException(status_code=400, detail="All three fields are required, and the base URL must start with https://.")

        recording = wire_capture.RecordingSession() if wire_capture else None
        client = ConversationsApiClient(
            base_url=base_url,
            bot_name=bot_name,
            bearer_provider=ApiKeyBearer(api_key),
            max_retries=3,
            session=recording,
        )
        # From here the wire pane shows only this connection's calls, so a poll
        # still in flight for the previous credential cannot reach it.
        previous_generation = wire_capture.hub.generation if wire_capture else None
        if wire_capture and recording:
            wire_capture.hub.activate(recording.generation)
        # Prove the credential before entering chat: cheapest real call.
        try:
            await asyncio.to_thread(
                lambda: client.get("/rest/v1/conversations", params={"limit": 1}).json()
            )
        except Exception as exc:
            if wire_capture:
                wire_capture.hub.activate(previous_generation)
            raise HTTPException(status_code=401, detail=_connect_error_hint(exc)) from exc

        session.reset()
        session.client = client
        session.base_url = base_url
        session.bot_name = bot_name
        session.connected_at = time.time()
        session.loop = asyncio.get_running_loop()
        session.polling_loop = PollingLoop(
            client=client,
            checkpoint=session.checkpoint,
            handler=lambda user_id, event: _dispatch_polled_event(user_id, event, client),
            interval_seconds=POLL_INTERVAL_SECONDS,
            # _first_poll sets the checkpoint, so history is never replayed here.
            first_run="replay_history",
        )
        session.polling_task = asyncio.create_task(_polling_task())
        log.info("connected: bot=%s base=%s", bot_name, base_url)
        return {"connected": True, "bot_name": bot_name, "base_url": base_url}

    @app.post("/api/disconnect")
    async def disconnect() -> dict:
        session.reset()
        if wire_capture:
            wire_capture.hub.activate(None)
        return {"connected": False}

    @app.post("/api/message")
    async def post_message(body: MessageIn) -> StreamingResponse:
        _require_connected()
        if bool(body.text or body.file_ids) == bool(body.callback_id):
            raise HTTPException(status_code=400, detail="Send text or files, or a callback_id.")
        session.last_message_at = time.time()
        frames = run_turn(
            session.client, body.conversation_id, body.text, body.callback_id,
            streaming=settings["streaming"], file_ids=body.file_ids,
        )
        return StreamingResponse(
            wire_capture.capture_turn(frames) if wire_capture else frames,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/feedback")
    async def submit_feedback(body: FeedbackIn) -> dict:
        client = _require_connected()
        path = (
            f"/rest/v1/conversations/{body.conversation_id}"
            f"/responses/{body.response_id}/messages/{body.message_id}/feedback"
        )
        payload = {"callback_id": body.callback_id}
        if body.additional_feedback and body.additional_feedback.strip():
            payload["additional_feedback"] = body.additional_feedback.strip()
        return {"ok": True, "result": await _upstream(lambda: client.post(path, json=payload).json())}

    @app.get("/api/conversations")
    async def conversations_index(limit: int = 20, cursor: Optional[str] = None, archived: bool = False) -> dict:
        client = _require_connected()
        return await _upstream(lambda: list_conversations(client, limit=limit, cursor=cursor, archived=archived))

    @app.patch("/api/conversations/{conversation_id}")
    async def conversation_update(conversation_id: str, body: ConversationPatchIn) -> dict:
        client = _require_connected()
        title = body.title.strip() if body.title is not None else None
        if title is not None and not title:
            raise HTTPException(status_code=400, detail="A title cannot be empty.")
        if title is None and body.archived is None:
            raise HTTPException(status_code=400, detail="Send a title or archived.")
        return await _upstream(lambda: update_conversation(client, conversation_id, title=title, archived=body.archived))

    @app.get("/api/conversations/{conversation_id}/messages")
    async def conversation_messages(conversation_id: str, limit: int = 50, cursor: Optional[str] = None) -> dict:
        client = _require_connected()
        body = await _upstream(lambda: list_messages(client, conversation_id, limit=limit, cursor=cursor))
        # The API returns newest first; the thread renders oldest first.
        messages = list(reversed(body.get("messages") or []))
        return {
            "messages": await asyncio.to_thread(lambda: [present_message(m) for m in messages]),
            "next_cursor": (body.get("metadata") or {}).get("next_cursor"),
        }

    @app.post("/api/modal")
    async def get_modal(body: ModalRef) -> dict:
        client = _require_connected()
        # Modals are served under /rest/v1 only.
        path = (
            f"/rest/v1/conversations/{body.conversation_id}"
            f"/responses/{body.response_id}/messages/{body.message_id}/modals/{body.modal_id}"
        )
        return present_modal(await _upstream(lambda: client.get(path).json(), _modal_error_hint))

    @app.post("/api/modal/refresh")
    async def refresh_modal(body: ModalRefreshIn) -> dict:
        client = _require_connected()
        # Re-resolves conditional fields after a `triggers_refresh` field changes.
        # Choice fields come back without `options`; the browser re-applies them.
        path = (
            f"/rest/v1/conversations/{body.conversation_id}"
            f"/responses/{body.response_id}/messages/{body.message_id}/modals/{body.modal_id}/refresh"
        )
        return present_modal(await _upstream(lambda: client.post(path, json={"values": body.values}).json(), _modal_error_hint))

    @app.post("/api/modal/submit")
    async def submit_modal(body: ModalSubmitIn) -> dict:
        client = _require_connected()
        path = (
            f"/rest/v1/conversations/{body.conversation_id}"
            f"/responses/{body.response_id}/messages/{body.message_id}/modals/{body.modal_id}/submit"
        )
        return await _upstream(lambda: client.post(path, json={"values": body.values}).json(), _modal_error_hint)

    @app.get("/api/events")
    async def notifications_stream(request: Request) -> StreamingResponse:
        _require_connected()
        return StreamingResponse(
            _notifications_sse(request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if wire_capture:
        wire_capture.register(app)
    if register_attachments:
        register_attachments(app, session)
    if register_test_notifications:
        register_test_notifications(app, session)

    return app


def _is_raw_upload(request: Request) -> bool:
    """A file sent as raw bytes with its name in ``X-File-Name``.

    The custom header makes a cross-site request need a CORS preflight, which
    this server never grants, so it carries the same protection as JSON.
    """
    return (
        request.headers.get("content-type", "") == "application/octet-stream"
        and bool(request.headers.get("x-file-name"))
    )


def _upstream_hint(exc: Exception) -> str:
    """Turn a client exception into something a person can act on."""
    if isinstance(exc, RateLimitedError):
        return (
            "Moveworks rate-limited this request. Retry in a few seconds. Reading "
            "conversation history in parallel uses up the limit fastest."
        )
    return str(exc)[:300] or exc.__class__.__name__


def _modal_error_hint(exc: Exception) -> str:
    if isinstance(exc, RequestRejectedError) and exc.status_code == 404:
        return (
            "Moveworks returned 404 for the modal. Modal ids are minted per message read, so re-read "
            "the message for a fresh one rather than reusing an old id. If every modal returns 404, "
            "check with your Moveworks account team that modals are available for your Moveworks instance."
        )
    return _upstream_hint(exc)


def _require_connected() -> ConversationsApiClient:
    if session.client is None:
        raise HTTPException(status_code=409, detail="Not connected. Enter your Bot Name and credential first.")
    return session.client


async def _upstream(call, hint=None):
    """Run a blocking API call; any failure becomes a 502 with something a
    person can act on. Callers capture the client first, so a disconnect
    mid-call cannot leave them holding None."""
    try:
        return await asyncio.to_thread(call)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=(hint or _upstream_hint)(exc)) from exc


def _connect_error_hint(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, AuthenticationError):
        return (
            "Moveworks rejected the API key (401). Check the key, and confirm it was created "
            "in the same Moveworks instance as the Bot Name."
        )
    if isinstance(exc, RequestRejectedError) and exc.status_code == 404:
        return (
            "The endpoint was not found (404). Check the base URL, and confirm with your Moveworks account team "
            "that the Conversations API is enabled for your Moveworks instance; requests return 404 until it is."
        )
    if "Name or service not known" in text or "nodename" in text or "Failed to resolve" in text:
        return "That base URL does not resolve. Check the data-center URL (for US production it is https://api.moveworks.ai)."
    return f"Connection failed: {text[:300]}"


async def _notifications_sse(request: Request) -> AsyncIterator[bytes]:
    queue: asyncio.Queue = asyncio.Queue()
    session.event_subscribers.append(queue)
    if wire_capture:
        wire_capture.hub.subscribe(queue)
    try:
        yield sse_frame("hello", {})
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield sse_frame("heartbeat", {})
                continue
            if isinstance(event, bytes):  # a wire frame from app.wire_capture
                yield event
                continue
            message = (event.raw.get("event_payload") or {}).get("message") or {}
            yield sse_frame(
                "notification",
                {
                    "event_id": event.event_id,
                    "conversation_id": event.conversation_id,
                    "created_at": event.created_at,
                    "actor": event.message_actor,
                    "text": present_notification_text(event.content_text),
                    # The whole message, so a same-conversation follow-up keeps
                    # its buttons, sources, feedback, and live agent attribution.
                    "message": await asyncio.to_thread(present_message, message) if message else None,
                },
            )
    finally:
        if wire_capture:
            wire_capture.hub.unsubscribe(queue)
        # A reconnect resets the session, so this queue may belong to the old one.
        if queue in session.event_subscribers:
            session.event_subscribers.remove(queue)


async def _polling_task() -> None:
    # This connection's objects, captured now: a reconnect replaces the session's,
    # and a poll still running in its thread must not write into the new ones.
    client, checkpoint, polling_loop, connected_at = (
        session.client, session.checkpoint, session.polling_loop, session.connected_at
    )
    # The first poll waits one interval so it does not compete with the connect
    # check and the first conversation-list read.
    first = True
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            if client and first:
                await asyncio.to_thread(_first_poll, client, connected_at, checkpoint)
                first = False
            elif polling_loop:
                # An API key is bound to one user, so one checkpoint covers every event.
                await asyncio.to_thread(polling_loop.poll_once, POLL_USER)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("polling tick failed; retrying")


def _first_poll(client: ConversationsApiClient, connected_at: float, checkpoint: InMemoryCheckpoint) -> None:
    """Set the checkpoint to the newest event, dispatching only events since connect.

    Connecting must not replay history as a burst of notifications, but an
    event that arrived during the first interval is new. One unfiltered read
    puts the newest events on the first page.
    """
    events = sorted(EventsPollingClient(client).fetch_page(limit=20).events, key=event_sort_key)
    for event in events:
        if event_sort_key(event)[0] >= connected_at - CONNECT_CLOCK_SKEW_SECONDS:
            _dispatch_polled_event(POLL_USER, event, client)
        checkpoint.advance(POLL_USER, event.event_id)


def _dispatch_polled_event(user_id: str, event: Event, client: Optional[ConversationsApiClient] = None) -> None:
    # Called from the polling thread: hand off to the loop so a waiting SSE
    # stream wakes now, not at its next heartbeat. The connection check runs on
    # the loop too, where a reconnect cannot interleave with it.
    loop = session.loop
    if loop is None:
        _deliver(event, client)
        return
    try:
        loop.call_soon_threadsafe(_deliver, event, client)
    except RuntimeError:
        pass  # the loop closed: the app is shutting down


def _deliver(event: Event, client: Optional[ConversationsApiClient]) -> None:
    if client is not None and session.client is not client:
        return  # a poll from a connection that has since been replaced
    for queue in list(session.event_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


app = build_app()


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--no-stream", action="store_true", help="submit and poll instead of streaming")
    args = parser.parse_args(argv)
    settings["streaming"] = not args.no_stream

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import uvicorn

    # Uvicorn reports a taken port only after "startup complete", so check first.
    try:
        with socket.socket() as probe:
            # The same option uvicorn sets, so the check fails exactly when uvicorn would.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((HOST, args.port))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        sys.exit(f"Port {args.port} is already in use. Stop the other process, or pass --port to choose another.")

    log.info("streaming %s", "on" if settings["streaming"] else "off (submit and poll)")
    uvicorn.run(app, host=HOST, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
