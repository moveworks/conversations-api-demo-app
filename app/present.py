"""Turn Conversations API objects into the shapes the browser renders.

Every message, whether it arrived on a live turn or from conversation
history, goes through ``present_message``, so the browser renders both the
same way and never sees raw API markup.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from moveworks_capi.responses import message_actions
from moveworks_capi.rendering import (
    citation_label,
    citation_summary,
    clean_label,
    form_link_citations,
    modal_html_to_markdown,
    normalize_markup,
)

try:  # Optional: delete app/inline_forms.py to render form cards below the answer instead.
    from app.inline_forms import answer_layout, form_citation_ids
except ImportError:  # pragma: no cover - exercised by deleting the module
    answer_layout = None
    form_citation_ids = None


def present_message(message: Dict[str, Any], *, response_id: str = "", resolve_links: bool = True) -> Dict[str, Any]:
    """A message as the browser renders it.

    Keys: ``message_id``, ``response_id``, ``actor``, ``text`` (CommonMark),
    ``files`` (names of files the user attached), ``is_action`` (a user turn
    that pressed a button rather than typed), ``layout`` (answer split around
    inline form cards, or None), ``sources``, ``actions``, ``handoff``,
    ``feedback``.

    A ``URL_ACTION`` that opens a cited form gets a ``form`` key (``title``,
    ``description``). Pairing makes one blocking request per short link (cached),
    so call this off the event loop, and pass ``resolve_links=False`` where
    actions are not rendered, such as streamed snapshots.
    """
    forms = form_link_citations(message) if resolve_links else {}
    actions = []
    for i, action in enumerate(message_actions(message)):
        shown = present_action(asdict(action))
        if i in forms:
            shown["form"] = {"title": citation_label(forms[i]), "description": citation_summary(forms[i])}
        actions.append(shown)
    return {
        "message_id": message.get("message_id", ""),
        "response_id": message.get("response_id") or response_id,
        "actor": message.get("actor", ""),
        "sender": present_sender(message.get("sender_info")),
        "text": normalize_markup(message_text(message)),
        "files": message_files(message),
        "is_action": (message.get("content") or {}).get("type") == "CALLBACK_ACTION_CONTENT",
        "layout": answer_layout(message) if answer_layout else None,
        "sources": present_sources(message),
        "actions": actions,
        "handoff": present_handoff(message.get("handoff") or {}),
        "feedback": {
            "helpful": ((message.get("feedback") or {}).get("helpful") or {}).get("callback_id"),
            "unhelpful": ((message.get("feedback") or {}).get("unhelpful") or {}).get("callback_id"),
        },
    }


def present_sender(sender_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Who wrote a ``LIVE_AGENT`` message, or None.

    ``sender_info`` is the only name the API gives a human agent; an actor the
    client does not recognise would otherwise read as the assistant.
    """
    if not sender_info or not sender_info.get("display_name"):
        return None
    return {"display_name": sender_info["display_name"], "avatar_url": sender_info.get("avatar_url") or ""}


def present_action(action: Dict[str, Any]) -> Dict[str, Any]:
    return {**action, "label": clean_label(action.get("label", ""))}


def present_sources(message: Dict[str, Any]) -> List[Dict[str, str]]:
    """Citations for the Sources list.

    One entry per document: the API cites each section of an article
    separately, so the same title and URL often appear several times. Forms
    the inline-forms module shows as cards are left out.
    """
    shown_as_cards = set(form_citation_ids(message)) if form_citation_ids else set()
    sources: List[Dict[str, str]] = []
    seen: set = set()
    for citation in message.get("citations") or []:
        if citation.get("citation_id") in shown_as_cards:
            continue
        display = citation.get("display") or {}
        title = citation_label(citation)
        url = citation.get("url") or ""
        if (title, url) in seen:
            continue
        seen.add((title, url))
        attributes = {a.get("key"): a.get("value") for a in display.get("attributes") or []}
        body = ((display.get("body") or {}).get("markdown_text") or {}).get("text") or ""
        sources.append({
            "title": title or url or "Source",
            "url": url,
            "snippet": " ".join(normalize_markup(body).split())[:240],
            "updated": attributes.get("UPDATE_DATETIME", ""),
            "state": attributes.get("TICKET_STATE", ""),
        })
    return sources


def present_handoff(handoff: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The Get Help menu from ``Message.handoff``, or an empty list."""
    categories = []
    for category in handoff.get("handoff_categories") or []:
        items = []
        for item in category.get("handoff_items") or []:
            entry: Dict[str, Any] = {"title": clean_label(item.get("title", "")), "kind": item.get("type", "")}
            if item.get("action"):
                entry["action"] = present_action(_raw_action(item["action"]))
            content = item.get("text") or {}
            if content:
                content_type = (content.get("type") or "").lower()
                entry["text"] = normalize_markup((content.get(content_type) or {}).get("text", ""))
            items.append(entry)
        categories.append({
            "name": clean_label(category.get("name", "")),
            "description": normalize_markup(category.get("description", "")),
            "items": items,
        })
    return categories


def present_modal(modal: Dict[str, Any]) -> Dict[str, Any]:
    """A modal with its title and DISPLAY field text converted to Markdown."""
    fields = []
    for field in modal.get("fields") or []:
        field = dict(field)
        text = (field.get("display") or {}).get("text")
        if field.get("type") == "DISPLAY" and isinstance(text, dict) and text.get("text"):
            field["display"] = {**field["display"], "text": {**text, "text": modal_html_to_markdown(text["text"])}}
        fields.append(field)
    return {**modal, "title": clean_label(modal.get("title", "")), "fields": fields}


def message_text(message: Dict[str, Any]) -> str:
    content = message.get("content") or {}
    body = content.get((content.get("type") or "").lower()) or content.get("markdown_text") or {}
    text = body.get("text") if isinstance(body, dict) else None
    return text if isinstance(text, str) else ""


def message_files(message: Dict[str, Any]) -> List[str]:
    """Names of files attached to a user turn (``plain_text.files``)."""
    plain_text = (message.get("content") or {}).get("plain_text") or {}
    return [f.get("file_name") or f.get("file_id", "") for f in plain_text.get("files") or []]


def _raw_action(raw: Dict[str, Any]) -> Dict[str, Any]:
    actions = message_actions({"actions": [raw]})
    return asdict(actions[0]) if actions else {}


def present_notification_text(text: Optional[str]) -> str:
    return normalize_markup(text or "")
