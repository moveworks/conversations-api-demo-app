"""Optional: stream turns over server-sent events.

With this module, replies arrive progressively: the reasoning trail updates
as the assistant works, and the answer renders as soon as it exists. Without
it (delete this file, or run with ``--no-stream``), ``app.turns`` submits the
message and polls until the response completes, then renders the answer at
once.
"""

from __future__ import annotations

from typing import AsyncIterator, Callable, Optional

from moveworks_capi.client import ConversationsApiClient
from moveworks_capi.streaming import (
    OutputDelta,
    ResponseCompleted,
    ResponseCreated,
    ResponseInProgress,
    stream_response,
)
from moveworks_capi.rendering import normalize_markup

from app.frames import sse_frame, to_async, wire
from app.present import present_message


async def stream_turn(
    client: ConversationsApiClient,
    conversation_id: str,
    text: Optional[str],
    callback_id: Optional[str],
    on_response_id: Callable[[str], None],
    on_answer: Callable[[], None] = lambda: None,
    on_completed: Callable[[], None] = lambda: None,
) -> AsyncIterator[bytes]:
    yield wire("send", "POST /responses/stream", (text or "")[:120] if text else f"callback_id={callback_id[:40]}...")
    stream = stream_response(client, conversation_id, text, callback_id=callback_id)
    async for event in to_async(stream):
        if isinstance(event, ResponseCreated):
            on_response_id(event.response_id)
            yield wire("recv", "sse RESPONSE_CREATED", event.response_id)
        elif isinstance(event, ResponseInProgress):
            yield wire("recv", "sse RESPONSE_IN_PROGRESS")
        elif isinstance(event, OutputDelta):
            yield wire("recv", f"sse OUTPUT_DELTA #{event.sequence_number} {event.output_type}", (event.text or "")[-90:])
            if event.output_type == "MESSAGE":
                on_answer()
                message = present_message(event.output.get("message") or {}, resolve_links=False)
                yield sse_frame("delta", {"output_type": "MESSAGE", "text": message["text"], "layout": message["layout"], "sources": message["sources"]})
            else:
                yield sse_frame("delta", {"output_type": event.output_type, "text": normalize_markup(event.text or "")})
        elif isinstance(event, ResponseCompleted):
            on_completed()
            yield wire("recv", f"sse RESPONSE_COMPLETED {event.status}")
            yield sse_frame("completed", {"status": event.status})
