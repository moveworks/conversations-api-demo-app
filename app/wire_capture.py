"""Optional: record the HTTP exchanges behind the wire pane's rows.

With this module and ``js/optional/wire.js``, each wire pane row that made a
Conversations API call expands to show the request and response as they
crossed the network: method, URL, headers, and bodies, with the raw
server-sent events for a streamed turn. Credentials never leave the server.
The ``Authorization`` header, cookies, signatures, API keys, and token fields
in bodies are replaced with ``[redacted]`` before anything is recorded.

Capture stays off until the wire pane loads and calls ``POST /api/wire``, so
without ``wire.js`` this module does no work. Delete it (together with
wire.js) to drop the expandable rows.

A call made during a chat turn belongs to that turn: ``capture_turn`` puts a
recorder in a context variable, which ``asyncio.to_thread`` copies into the
worker thread that makes the call, and the exchange is attached to the turn's
latest ``send`` row on the same response stream. Any other call (connect,
sidebar, history, modals, feedback, event polling) is published to the
browser's ``/api/events`` stream.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import re
import threading
import time
from collections import deque
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from fastapi import FastAPI
from pydantic import BaseModel

from app.frames import sse_frame

MAX_BODY_BYTES = 64 * 1024
REDACTED = "[redacted]"

SECRET_HEADER = re.compile(r"authorization|cookie|signature|secret|token|api[-_]?key", re.IGNORECASE)
SECRET_FIELDS = frozenset({
    "access_token", "api_key", "apikey", "assertion", "client_assertion", "client_secret",
    "id_token", "password", "private_key", "refresh_token", "secret", "subject_token", "token",
})
JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*")


class TurnRecorder:
    """Exchanges made during one chat turn, tagged with the row that made them."""

    def __init__(self) -> None:
        self.row: Optional[str] = None
        self.done: Deque[Tuple[Optional[str], Dict[str, Any]]] = deque()

    def add(self, row: Optional[str], exchange: Dict[str, Any]) -> None:
        self.done.append((row, exchange))


class _Hub:
    """Exchanges made outside a turn, fanned out to ``/api/events`` streams.

    Calls run on worker threads, so frames reach each stream's queue through
    its event loop. Frames recorded with no stream open (the connect check
    runs before the browser opens one) wait in a short backlog.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.subscribers: List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self.backlog: Deque[bytes] = deque(maxlen=20)

    def publish(self, frame: bytes) -> None:
        with self.lock:
            if not self.subscribers:
                self.backlog.append(frame)
                return
            for loop, queue in self.subscribers:
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, frame)
                except RuntimeError:  # the stream's loop has closed
                    pass

    def subscribe(self, queue: asyncio.Queue) -> None:
        with self.lock:
            self.subscribers.append((asyncio.get_running_loop(), queue))
            while self.backlog:
                queue.put_nowait(self.backlog.popleft())

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self.lock:
            self.subscribers = [(loop, q) for loop, q in self.subscribers if q is not queue]

    def clear(self) -> None:
        with self.lock:
            self.backlog.clear()


hub = _Hub()
state = {"enabled": False}
_turn: contextvars.ContextVar[Optional[TurnRecorder]] = contextvars.ContextVar("wire_turn", default=None)


class RecordingSession(requests.Session):
    """A ``requests.Session`` that records each exchange while capture is on.

    Pass it to ``ConversationsApiClient(session=...)``. A streamed response is
    recorded when it is closed, with the event stream as read.
    """

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        if not state["enabled"]:
            return super().send(request, **kwargs)
        recorder = _turn.get()
        row = recorder.row if recorder else None
        started = time.monotonic()
        try:
            response = super().send(request, **kwargs)
        except requests.RequestException as exc:
            _deliver(recorder, row, exchange(request, None, started, error=str(exc)))
            raise
        if kwargs.get("stream"):
            _record_when_closed(response, recorder, row, started)
        else:
            _deliver(recorder, row, exchange(request, response, started))
        return response


def _record_when_closed(
    response: requests.Response, recorder: Optional[TurnRecorder], row: Optional[str], started: float
) -> None:
    received = bytearray()
    total = [0]
    read = response.iter_content
    close = response.close

    def iter_content(chunk_size: Any = 1, decode_unicode: bool = False):
        for chunk in read(chunk_size, decode_unicode):
            raw = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            total[0] += len(raw)
            if len(received) < MAX_BODY_BYTES:
                received.extend(raw[: MAX_BODY_BYTES - len(received)])
            yield chunk

    def close_and_record() -> None:
        response.iter_content = read  # type: ignore[method-assign]
        response.close = close  # type: ignore[method-assign]
        close()
        body = bytes(received).decode("utf-8", errors="replace")
        _deliver(recorder, row, exchange(response.request, response, started, body=body, body_size=total[0]))

    response.iter_content = iter_content  # type: ignore[method-assign]
    response.close = close_and_record  # type: ignore[method-assign]


def _deliver(recorder: Optional[TurnRecorder], row: Optional[str], record: Dict[str, Any]) -> None:
    if recorder is not None:
        recorder.add(row, record)
    else:
        hub.publish(_frame(None, record, background=True))


def exchange(
    request: requests.PreparedRequest,
    response: Optional[requests.Response],
    started: float,
    *,
    body: Optional[str] = None,
    body_size: Optional[int] = None,
    error: str = "",
) -> Dict[str, Any]:
    """One request and its response, redacted and size-capped."""
    record: Dict[str, Any] = {
        "method": request.method or "",
        "url": redact_url(request.url or ""),
        "request_headers": redact_headers(request.headers),
        "request_body": format_body(request.body, request.headers.get("Content-Type", "")),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
    if error:
        record["error"] = redact_text(error)
    if response is not None:
        content_type = response.headers.get("Content-Type", "")
        record["status"] = response.status_code
        record["reason"] = response.reason or ""
        record["response_headers"] = redact_headers(response.headers)
        if body is not None:
            record["streamed"] = True
            record["response_body"] = _cap(redact_text(body), body_size)
        else:
            record["response_body"] = format_body(response.content, content_type)
    return record


def redact_headers(headers: Any) -> Dict[str, str]:
    return {name: REDACTED if SECRET_HEADER.search(name) else str(value) for name, value in headers.items()}


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(k.lower() in SECRET_FIELDS for k, _ in query):
        return url
    query = [(k, REDACTED if k.lower() in SECRET_FIELDS else v) for k, v in query]
    return urlunsplit(parts._replace(query=urlencode(query, safe="[]")))


def redact_text(text: str) -> str:
    return JWT.sub(REDACTED, text)


def format_body(body: Any, content_type: str) -> str:
    """A request or response body as text: JSON pretty-printed, secrets redacted."""
    if not body:
        return ""
    raw = body if isinstance(body, bytes) else str(body).encode("utf-8")
    text = raw.decode("utf-8", errors="replace")
    if "application/x-www-form-urlencoded" in content_type:
        pairs = parse_qsl(text, keep_blank_values=True)
        text = "\n".join(f"{k}={REDACTED if k.lower() in SECRET_FIELDS else v}" for k, v in pairs)
    else:
        try:
            text = json.dumps(_redact_fields(json.loads(text)), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    return _cap(redact_text(text), len(raw))


def _redact_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: REDACTED if str(k).lower() in SECRET_FIELDS else _redact_fields(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_fields(v) for v in value]
    return value


def _cap(text: str, size: Optional[int]) -> str:
    encoded = text.encode("utf-8")
    size = max(size or 0, len(encoded))
    if len(encoded) <= MAX_BODY_BYTES and size <= MAX_BODY_BYTES:
        return text
    kept = encoded[:MAX_BODY_BYTES].decode("utf-8", errors="ignore")
    return f"{kept}\n[truncated: showing the first {MAX_BODY_BYTES:,} of {size:,} bytes]"


def _frame(row: Optional[str], record: Dict[str, Any], *, background: bool = False) -> bytes:
    if row is not None:
        return sse_frame("wire", {"attach": row, "exchange": record})
    path = urlsplit(record["url"]).path
    return sse_frame("wire", {
        "t": round(time.time() % 100000, 2),
        "dir": "send",
        "label": f"{record['method']} {path}",
        "detail": str(record.get("status") or record.get("error") or ""),
        "background": background,
        "exchange": record,
    })


def _send_row_id(frame: bytes) -> Optional[str]:
    if not frame.startswith(b"event: wire\n"):
        return None
    try:
        data = json.loads(frame.split(b"data: ", 1)[1])
    except (IndexError, ValueError):
        return None
    return data.get("id") if data.get("dir") == "send" else None


async def capture_turn(frames: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Pass a turn's frames through, adding the exchanges its calls made.

    A ``send`` row is yielded before the call it announces, so an exchange
    belongs to the latest ``send`` row seen when the call started.
    """
    if not state["enabled"]:
        async for frame in frames:
            yield frame
        return
    recorder = TurnRecorder()
    previous = _turn.get()
    _turn.set(recorder)
    try:
        async for frame in frames:
            for pending in _drain(recorder):
                yield pending
            row = _send_row_id(frame)
            if row is not None:
                recorder.row = row
            yield frame
        for pending in _drain(recorder):
            yield pending
    finally:
        _turn.set(previous)


def _drain(recorder: TurnRecorder) -> List[bytes]:
    frames = []
    while recorder.done:
        row, record = recorder.done.popleft()
        frames.append(_frame(row, record))
    return frames


class WireIn(BaseModel):
    capture: bool


def register(app: FastAPI) -> None:
    @app.post("/api/wire")
    async def set_capture(body: WireIn) -> dict:
        state["enabled"] = body.capture
        if not body.capture:
            hub.clear()
        return {"capture": state["enabled"]}
