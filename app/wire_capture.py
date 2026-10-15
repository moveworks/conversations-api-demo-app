"""Optional: record the HTTP exchanges behind the wire pane's rows.

With this module and ``js/optional/wire.js``, each wire pane row that made a
Conversations API call expands to show the request and response as they
crossed the network: method, URL, headers, and bodies, with the raw
server-sent events for a streamed turn. Credentials are redacted on the
server: headers, body fields, and query parameters whose names look like
credentials (see ``is_secret_name``), and JWTs and bearer tokens anywhere,
are replaced with ``[redacted]`` before anything is recorded.

Capture stays off until the wire pane loads and calls ``POST /api/wire``,
and turns off again when the last browser event stream closes, so without
``wire.js`` this module does no work. Delete it (together with
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
import itertools
import json
import re
import threading
import time
from collections import deque
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, unquote_plus, urlsplit

import requests
from fastapi import FastAPI
from pydantic import BaseModel

from app.frames import sse_frame

MAX_BODY_BYTES = 64 * 1024
REDACTED = "[redacted]"

SECRET_NAME_PARTS = (
    "token", "secret", "password", "assertion", "signature", "apikey", "credential", "codeverifier",
    "authorization", "cookie", "jwt",
)
# OAuth sends the authorization code as ``code``; only form bodies and query
# strings use it that way, while JSON error bodies carry a harmless ``code``.
SECRET_FORM_NAMES = frozenset({"code", "sig"})
# Names that contain a credential word but never hold one.
NOT_SECRET_NAMES = frozenset({"tokentype"})
JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*")
BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]+")
SECRET_PAIR = re.compile(
    r"(?i)(?<![\w-])([\w-]*(?:token|secret|password|assertion|signature|api[-_]?key|credential|code[-_]?verifier|jwt)[\w-]*"
    r"|code|sig)=[^&\s\"']+"
)
JSON_STRING_PAIR = re.compile(r'("([^"\\]+)"\s*:\s*)"(?:[^"\\]|\\.)*"?')


def _normalized(name: str) -> str:
    return name.lower().replace("-", "").replace("_", "")


def is_secret_name(name: str, *, form: bool = False) -> bool:
    """Whether a header, field, or parameter name looks like it holds a credential."""
    normalized = _normalized(str(name))
    if normalized in NOT_SECRET_NAMES:
        return False
    return any(part in normalized for part in SECRET_NAME_PARTS) or (form and normalized in SECRET_FORM_NAMES)


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

    Each ``RecordingSession`` has a generation. Only the current one's frames
    are kept, so a poll still in flight from a replaced connection never
    reaches the next one.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.subscribers: List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self.backlog: Deque[Tuple[int, bytes]] = deque(maxlen=20)
        self.generation: Optional[int] = None

    def activate(self, generation: Optional[int]) -> None:
        """Keep only frames from ``generation`` (None keeps nothing)."""
        with self.lock:
            self.generation = generation
            kept = [(g, f) for g, f in self.backlog if g == generation]
            self.backlog.clear()
            self.backlog.extend(kept)

    def publish(self, generation: int, frame: bytes) -> None:
        with self.lock:
            if generation != self.generation:
                return
            if not self.subscribers:
                self.backlog.append((generation, frame))
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
                queue.put_nowait(self.backlog.popleft()[1])

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Drop a stream. With none left no browser shows the pane, so capture stops."""
        with self.lock:
            self.subscribers = [(loop, q) for loop, q in self.subscribers if q is not queue]
            if not self.subscribers:
                state["enabled"] = False
                self.backlog.clear()


_generations = itertools.count(1)
hub = _Hub()
state = {"enabled": False}
_turn: contextvars.ContextVar[Optional[TurnRecorder]] = contextvars.ContextVar("wire_turn", default=None)


class RecordingSession(requests.Session):
    """A ``requests.Session`` that records each exchange while capture is on.

    Pass it to ``ConversationsApiClient(session=...)``, and call
    ``hub.activate(session.generation)`` for the connection the browser
    should see. A streamed response is recorded when it is closed, with the
    event stream as read.
    """

    def __init__(self) -> None:
        super().__init__()
        self.generation = next(_generations)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        if not state["enabled"]:
            return super().send(request, **kwargs)
        recorder = _turn.get()
        row = recorder.row if recorder else None
        started = time.monotonic()
        try:
            response = super().send(request, **kwargs)
        except requests.RequestException as exc:
            _deliver(self.generation, recorder, row, exchange(request, None, started, error=str(exc)))
            raise
        if kwargs.get("stream"):
            _record_when_closed(response, self.generation, recorder, row, started)
        else:
            _deliver(self.generation, recorder, row, exchange(request, response, started))
        return response


def _record_when_closed(
    response: requests.Response, generation: int, recorder: Optional[TurnRecorder], row: Optional[str], started: float
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
        _deliver(generation, recorder, row, exchange(response.request, response, started, body=body, body_size=total[0]))

    response.iter_content = iter_content  # type: ignore[method-assign]
    response.close = close_and_record  # type: ignore[method-assign]


def _deliver(generation: int, recorder: Optional[TurnRecorder], row: Optional[str], record: Dict[str, Any]) -> None:
    if recorder is not None:
        recorder.add(row, record)
    else:
        hub.publish(generation, _frame(None, record, background=True))


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
        "request_body": format_body(request.body),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
    if error:
        record["error"] = redact_text(error)
    if response is not None:
        record["status"] = response.status_code
        record["reason"] = response.reason or ""
        record["response_headers"] = redact_headers(response.headers)
        if body is not None:
            record["streamed"] = True
            record["response_body"] = _cap(redact_stream(body), body_size)
        else:
            record["response_body"] = format_body(response.content)
    return record


def redact_headers(headers: Any) -> Dict[str, str]:
    return {name: REDACTED if is_secret_name(name) else redact_url(str(value)) for name, value in headers.items()}


def redact_url(url: str) -> str:
    """A URL with credential-like query values, JWTs, and bearer tokens redacted.

    Also safe on any header value or message that may contain a URL.
    """
    head, sep, query = url.partition("?")
    if sep:
        query = "&".join(_redact_pair(pair) for pair in query.split("&"))
    return redact_text(head + sep + query)


def _redact_pair(pair: str) -> str:
    name, sep, _ = pair.partition("=")
    return f"{name}={REDACTED}" if sep and is_secret_name(unquote_plus(name), form=True) else pair


def _redact_json_pair(match: re.Match) -> str:
    return f'{match.group(1)}"{REDACTED}"' if is_secret_name(match.group(2)) else match.group(0)


def redact_text(text: str) -> str:
    text = JWT.sub(REDACTED, text)
    text = BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    # JSON fields in text that does not parse whole, such as a truncated event.
    text = JSON_STRING_PAIR.sub(_redact_json_pair, text)
    return SECRET_PAIR.sub(lambda m: f"{m.group(1)}={REDACTED}", text)


def redact_stream(body: str) -> str:
    """A raw SSE stream with each ``data:`` event's JSON redacted field by field."""
    lines = []
    for line in body.split("\n"):
        prefix, sep, payload = line.partition("data:")
        if sep and not prefix:
            try:
                event = json.loads(payload)
            except ValueError:
                event = None
            redacted = _redact_fields(event)
            if redacted != event:
                # Rewritten only when a field was redacted, so clean events stay byte-for-byte raw.
                line = f"data: {json.dumps(redacted, ensure_ascii=False)}"
        lines.append(line)
    return redact_text("\n".join(lines))


def format_body(body: Any) -> str:
    """A request or response body as text: JSON pretty-printed, secrets redacted.

    A body that is not JSON but parses as form fields (``a=1&b=2``) is shown
    one field per line, whatever its ``Content-Type`` says.
    """
    if not body:
        return ""
    raw = body if isinstance(body, bytes) else str(body).encode("utf-8")
    text = raw.decode("utf-8", errors="replace")
    try:
        text = json.dumps(_redact_fields(json.loads(text)), indent=2, ensure_ascii=False)
    except ValueError:
        pairs = _form_pairs(text)
        if pairs is not None:
            text = "\n".join(f"{k}={REDACTED if is_secret_name(k, form=True) else v}" for k, v in pairs)
    return _cap(redact_text(text), len(raw))


def _form_pairs(text: str) -> Optional[List[Tuple[str, str]]]:
    if "=" not in text or any(c in text for c in " \n\t"):
        return None
    try:
        return parse_qsl(text, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None


def _redact_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: REDACTED if is_secret_name(k) else _redact_fields(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_fields(v) for v in value]
    if isinstance(value, str) and value[:1] in ("{", "["):
        # JSON carried in a string field, such as a webhook payload.
        try:
            nested = json.loads(value)
        except ValueError:
            return value
        return json.dumps(_redact_fields(nested), ensure_ascii=False)
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
        return {"capture": state["enabled"]}
