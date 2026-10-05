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
import logging
import sys
import time
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from moveworks_capi.auth import ApiKeyBearer
from moveworks_capi.client import ConversationsApiClient
from moveworks_capi.conversations import list_conversations, list_messages, update_conversation
from moveworks_capi.errors import AuthenticationError, ConversationsApiError, RateLimitedError, RequestRejectedError
from moveworks_capi.notifications import Event, InMemoryCheckpoint, PollingLoop

from app.frames import sse_frame
from app.present import present_message, present_modal, present_notification_text
from app.turns import run_turn

try:  # Optional: delete app/test_notifications.py to drop the "send a test notification" panel.
    from app.test_notifications import register as register_test_notifications
except ImportError:  # pragma: no cover - exercised by deleting the module
    register_test_notifications = None

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
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
        # A fresh checkpoint starts at the user's first event ever. The first
        # poll only catches up to now, so connecting does not replay history
        # as a burst of notifications.
        self.catching_up: bool = True

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
            if origin and urlparse(origin).hostname not in LOCAL_HOSTNAMES:
                return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"detail": "Send JSON."}, status_code=415)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

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

        client = ConversationsApiClient(
            base_url=base_url,
            bot_name=bot_name,
            bearer_provider=ApiKeyBearer(api_key),
            max_retries=3,
        )
        # Prove the credential before entering chat: cheapest real call.
        try:
            await asyncio.to_thread(
                lambda: client.get("/rest/v1/conversations", params={"limit": 1}).json()
            )
        except Exception as exc:
            raise HTTPException(status_code=401, detail=_connect_error_hint(exc)) from exc

        session.reset()
        session.client = client
        session.base_url = base_url
        session.bot_name = bot_name
        session.polling_loop = PollingLoop(
            client=client,
            checkpoint=InMemoryCheckpoint(),
            handler=_dispatch_polled_event,
            interval_seconds=POLL_INTERVAL_SECONDS,
        )
        session.polling_task = asyncio.create_task(_polling_task())
        log.info("connected: bot=%s base=%s", bot_name, base_url)
        return {"connected": True, "bot_name": bot_name, "base_url": base_url}

    @app.post("/api/disconnect")
    async def disconnect() -> dict:
        session.reset()
        return {"connected": False}

    @app.post("/api/message")
    async def post_message(body: MessageIn) -> StreamingResponse:
        _require_connected()
        if bool(body.text) == bool(body.callback_id):
            raise HTTPException(status_code=400, detail="Send either text or callback_id.")
        session.last_message_at = time.time()
        return StreamingResponse(
            run_turn(session.client, body.conversation_id, body.text, body.callback_id, streaming=settings["streaming"]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/feedback")
    async def submit_feedback(body: FeedbackIn) -> dict:
        _require_connected()
        path = (
            f"/rest/v1/conversations/{body.conversation_id}"
            f"/responses/{body.response_id}/messages/{body.message_id}/feedback"
        )
        payload = {"callback_id": body.callback_id}
        if body.additional_feedback and body.additional_feedback.strip():
            payload["additional_feedback"] = body.additional_feedback.strip()
        try:
            result = await asyncio.to_thread(lambda: session.client.post(path, json=payload).json())
        except ConversationsApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "result": result}

    @app.get("/api/conversations")
    async def conversations_index(limit: int = 20, cursor: Optional[str] = None, archived: bool = False) -> dict:
        _require_connected()
        try:
            return await asyncio.to_thread(
                lambda: list_conversations(session.client, limit=limit, cursor=cursor, archived=archived)
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_upstream_hint(exc)) from exc

    @app.patch("/api/conversations/{conversation_id}")
    async def conversation_update(conversation_id: str, body: ConversationPatchIn) -> dict:
        _require_connected()
        title = body.title.strip() if body.title is not None else None
        if title is not None and not title:
            raise HTTPException(status_code=400, detail="A title cannot be empty.")
        if title is None and body.archived is None:
            raise HTTPException(status_code=400, detail="Send a title or archived.")
        try:
            return await asyncio.to_thread(
                lambda: update_conversation(session.client, conversation_id, title=title, archived=body.archived)
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_upstream_hint(exc)) from exc

    @app.get("/api/conversations/{conversation_id}/messages")
    async def conversation_messages(conversation_id: str, limit: int = 50, cursor: Optional[str] = None) -> dict:
        _require_connected()
        try:
            body = await asyncio.to_thread(
                lambda: list_messages(session.client, conversation_id, limit=limit, cursor=cursor)
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_upstream_hint(exc)) from exc
        # The API returns newest first; the thread renders oldest first.
        messages = list(reversed(body.get("messages") or []))
        return {
            "messages": await asyncio.to_thread(lambda: [present_message(m) for m in messages]),
            "next_cursor": (body.get("metadata") or {}).get("next_cursor"),
        }

    @app.post("/api/modal")
    async def get_modal(body: ModalRef) -> dict:
        _require_connected()
        # Modals are served under /rest/v1 only.
        path = (
            f"/rest/v1/conversations/{body.conversation_id}"
            f"/responses/{body.response_id}/messages/{body.message_id}/modals/{body.modal_id}"
        )
        try:
            return present_modal(await asyncio.to_thread(lambda: session.client.get(path).json()))
        except ConversationsApiError as exc:
            raise HTTPException(status_code=502, detail=_modal_error_hint(exc)) from exc

    @app.post("/api/modal/refresh")
    async def refresh_modal(body: ModalRefreshIn) -> dict:
        _require_connected()
        # Re-resolves conditional fields after a `triggers_refresh` field changes.
        # Choice fields come back without `options`; the browser re-applies them.
        path = (
            f"/rest/v1/conversations/{body.conversation_id}"
            f"/responses/{body.response_id}/messages/{body.message_id}/modals/{body.modal_id}/refresh"
        )
        try:
            return present_modal(await asyncio.to_thread(
                lambda: session.client.post(path, json={"values": body.values}).json()
            ))
        except ConversationsApiError as exc:
            raise HTTPException(status_code=502, detail=_modal_error_hint(exc)) from exc

    @app.post("/api/modal/submit")
    async def submit_modal(body: ModalSubmitIn) -> dict:
        _require_connected()
        path = (
            f"/rest/v1/conversations/{body.conversation_id}"
            f"/responses/{body.response_id}/messages/{body.message_id}/modals/{body.modal_id}/submit"
        )
        try:
            return await asyncio.to_thread(
                lambda: session.client.post(path, json={"values": body.values}).json()
            )
        except ConversationsApiError as exc:
            raise HTTPException(status_code=502, detail=_modal_error_hint(exc)) from exc

    @app.get("/api/events")
    async def notifications_stream(request: Request) -> StreamingResponse:
        _require_connected()
        return StreamingResponse(
            _notifications_sse(request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if register_test_notifications:
        register_test_notifications(app, session)

    return app


def _upstream_hint(exc: Exception) -> str:
    """Turn a client exception into something a person can act on."""
    if isinstance(exc, RateLimitedError):
        return (
            "Moveworks rate-limited this request. Fetch conversation history one "
            "at a time rather than in parallel, and retry in a few seconds."
        )
    return str(exc)[:300] or exc.__class__.__name__


def _modal_error_hint(exc: Exception) -> str:
    if isinstance(exc, RequestRejectedError) and exc.status_code == 404:
        return (
            "Moveworks returned 404 for the modal. Modal ids are minted per message read, so re-read "
            "the message for a fresh one rather than reusing an old id. If every modal returns 404, "
            "check with your Moveworks account team that modals are available for your Moveworks instance."
        )
    return str(exc)[:300]


def _require_connected() -> None:
    if not session.connected:
        raise HTTPException(status_code=409, detail="Not connected. Enter your Bot Name and credential first.")


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
        # A reconnect resets the session, so this queue may belong to the old one.
        if queue in session.event_subscribers:
            session.event_subscribers.remove(queue)


async def _polling_task() -> None:
    # An API key is bound to one user, so one checkpoint covers every event.
    while True:
        try:
            if session.polling_loop:
                await asyncio.to_thread(session.polling_loop.poll_once, "app-user")
                session.catching_up = False
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("polling tick failed; retrying")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def _dispatch_polled_event(user_id: str, event: Event) -> None:
    if session.catching_up:
        return
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

    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import uvicorn

    log.info("streaming %s", "on" if settings["streaming"] else "off (submit and poll)")
    uvicorn.run(app, host=HOST, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
