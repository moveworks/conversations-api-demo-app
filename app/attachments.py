"""Optional: attach files to chat turns and to modal FILE fields.

With this module and ``js/optional/attachments.js``, the composer gets an
attach button (plus drag and drop, and paste), and modal FILE fields accept
uploads. The browser sends each file to ``POST /api/files``, which uploads it
with the Files API and returns the File resource; its ``id`` then goes out as
``chat.files`` on the turn, or as a FILE field's value on submit.

The Files API is in alpha and enabled per Moveworks instance, and uploaded
files expire 24 hours after they are created. Without this module the
composer has no attach button and FILE fields show as unsupported.

The browser sends the bytes as ``application/octet-stream`` with the name in
an ``X-File-Name`` header (see ``server._is_raw_upload``), which keeps the
server free of a multipart parser. The name is checked against the Files
API's filename rules, and the 100 MB limit is enforced while the body is
read, before anything is forwarded. The read goes to a spooled temporary
file, which bounds only this server's read-ahead: ``upload_file`` reads the
file into memory to build the multipart request it forwards.
"""

from __future__ import annotations

import asyncio
import tempfile
import unicodedata
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from starlette.requests import ClientDisconnect

from moveworks_capi.errors import ConversationsApiError, RateLimitedError, RequestRejectedError
from moveworks_capi.files import MAX_UPLOAD_BYTES, upload_file

# Smaller files stay in memory; larger ones spill to a temporary file.
SPOOL_IN_MEMORY_BYTES = 8 * 1024 * 1024
# A 100 MB upload over a slow uplink takes minutes, longer than the client's default timeout.
UPLOAD_TIMEOUT_SECONDS = 300.0
MAX_FILENAME_CHARACTERS = 255
BIDI_FORMATTING_CHARACTERS = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
PASSED_THROUGH_STATUSES = {400, 403, 413, 422}


def register(app: FastAPI, session: Any) -> None:
    @app.get("/api/files")
    async def capability() -> dict:
        # attachments.js shows its UI only when this answers.
        return {"enabled": True}

    @app.post("/api/files")
    async def upload(request: Request) -> dict:
        # Kept for the whole upload, so a reconnect meanwhile cannot switch credentials under it.
        client = session.client
        if client is None:
            raise HTTPException(status_code=409, detail="Not connected. Enter your Bot Name and credential first.")
        name = unicodedata.normalize("NFC", unquote(request.headers.get("x-file-name", ""))).strip()
        problem = filename_problem(name)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=_too_large(name))

        with tempfile.SpooledTemporaryFile(max_size=SPOOL_IN_MEMORY_BYTES) as spool:
            size = 0
            try:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail=_too_large(name))
                    spool.write(chunk)
            except ClientDisconnect:
                # The browser went away mid-upload; nothing is forwarded.
                raise HTTPException(status_code=400, detail="The upload was interrupted.") from None
            if not size:
                raise HTTPException(status_code=400, detail=f"{name} is empty.")
            spool.seek(0)
            try:
                uploaded = await asyncio.to_thread(
                    upload_file, client, spool, filename=name, timeout=UPLOAD_TIMEOUT_SECONDS,
                )
            except ValueError as exc:  # the library's own filename and size checks
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RateLimitedError as exc:
                raise HTTPException(status_code=429, detail="Moveworks rate-limited the upload. Retry in a few seconds.") from exc
            except RequestRejectedError as exc:
                raise HTTPException(status_code=_status_for(exc), detail=_upload_error_hint(exc)) from exc
            except ConversationsApiError as exc:
                raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc
        return {
            "id": uploaded.id,
            "name": uploaded.name,
            "content_type": uploaded.content_type,
            "size_bytes": uploaded.size_bytes,
            "expire_time": uploaded.expire_time,
        }


def filename_problem(name: str) -> Optional[str]:
    """Why a (NFC-normalized, trimmed) name breaks the Files API's filename rule, or None."""
    if not name:
        return "Send the file name in the X-File-Name header."
    if len(name) > MAX_FILENAME_CHARACTERS:
        return f"File names can be at most {MAX_FILENAME_CHARACTERS} characters."
    if name in (".", ".."):
        return f"{name!r} is not a valid file name."
    if "/" in name or "\\" in name:
        return "File names cannot contain / or \\."
    if any(unicodedata.category(char) == "Cc" or char in BIDI_FORMATTING_CHARACTERS for char in name):
        return "File names cannot contain control or bidirectional formatting characters."
    return None


def _too_large(name: str) -> str:
    return f"{name} is larger than the 100 MB limit."


def _status_for(exc: RequestRejectedError) -> int:
    return exc.status_code if exc.status_code in PASSED_THROUGH_STATUSES else 502


def _upload_error_hint(exc: RequestRejectedError) -> str:
    if exc.status_code == 404:
        return (
            "Moveworks returned 404 for the upload. The Files API is in alpha and enabled per Moveworks "
            "instance; ask your Moveworks account team to turn it on."
        )
    return str(exc)[:300]
