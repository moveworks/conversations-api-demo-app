"""Optional: send yourself a proactive notification from inside the app.

Proactive messages originate inside Moveworks, so the only documented way to
produce one on demand is an Agent Studio listener whose plugin ends in a
``notify`` step (see ``examples/trigger_listener.py`` in the starter). This module POSTs to
that listener, which then messages the recipient; the notification comes back
through the same ``/events`` polling the app already does.

Configured from ``.env`` because the listener is test plumbing, not part of
connecting to a Moveworks instance:

    MW_TEST_LISTENER_URL     the listener URL
    MW_TEST_LISTENER_SECRET  the listener's HMAC secret, when it is secured
    MW_TEST_NOTIFY_EMAIL     default recipient (the API key's user)

Delete this file, and ``static/js/optional/test-notify.js`` plus its import
in ``static/js/main.js``, to drop the feature.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict

import requests
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
MAX_DELAY_SECONDS = 60
SIGNATURE_HEADER = "X-Signature"


class TestNotificationIn(BaseModel):
    email: str
    message: str
    delay_seconds: int = 0


def listener_config() -> Dict[str, str]:
    """Settings from the process environment, falling back to ``.env``."""
    file_values = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    get = lambda key: (os.environ.get(key) or file_values.get(key) or "").strip()
    return {
        "url": get("MW_TEST_LISTENER_URL"),
        "secret": get("MW_TEST_LISTENER_SECRET"),
        "email": get("MW_TEST_NOTIFY_EMAIL"),
    }


def signed_headers(body: bytes, secret: str) -> Dict[str, str]:
    """Headers that let a secured listener verify the request came from us."""
    if not secret:
        return {}
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {SIGNATURE_HEADER: digest}


def send_to_listener(
    url: str,
    secret: str,
    payload: Dict[str, Any],
    post: Callable[..., Any] = requests.post,
) -> Dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", **signed_headers(body, secret)}
    reply = post(url, data=body, headers=headers, timeout=15)
    return {"status": reply.status_code, "body": reply.text[:300]}


def register(app: FastAPI, session: Any) -> None:
    @app.get("/api/test-notification")
    async def test_notification_status() -> dict:
        config = listener_config()
        last = session.last_message_at
        return {
            "configured": bool(config["url"]),
            "secured": bool(config["secret"]),
            "email": config["email"],
            "seconds_since_last_message": None if last is None else round(time.time() - last),
        }

    @app.post("/api/test-notification")
    async def send_test_notification(body: TestNotificationIn) -> dict:
        config = listener_config()
        if not config["url"]:
            raise HTTPException(status_code=409, detail="Set MW_TEST_LISTENER_URL in .env to send test notifications.")
        email, message = body.email.strip(), body.message.strip()
        if not email or not message:
            raise HTTPException(status_code=400, detail="A recipient email and a message are required.")
        delay = max(0, min(body.delay_seconds, MAX_DELAY_SECONDS))
        if delay:
            await asyncio.sleep(delay)
        sent_at = time.time()
        try:
            result = await asyncio.to_thread(
                send_to_listener, config["url"], config["secret"], {"email": email, "message": message}
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach the listener: {exc}") from exc
        if result["status"] == 429:
            raise HTTPException(status_code=429, detail="The listener is rate-limited (unsecured listeners accept about one request per ten seconds per Moveworks instance). Wait and retry.")
        if result["status"] >= 400:
            raise HTTPException(status_code=502, detail=f"The listener returned {result['status']}: {result['body']}")
        return {"sent_at": sent_at, "listener_status": result["status"]}

