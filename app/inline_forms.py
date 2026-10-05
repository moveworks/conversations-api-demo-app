"""Optional: place form cards inside the answer, before its closing question.

Message text can end with a plain-text copy of each offered form (see
``moveworks_capi.rendering``). Without this module the app renders the text
as sent and the form cards below the answer. With it, the copy is removed and every form card for the message is grouped after the
paragraph that first mentions a form.

Delete this file, and ``static/js/optional/inline-forms.js`` plus its import
in ``static/js/main.js``, to drop the behavior.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from moveworks_capi.rendering import citation_label, layout_message, normalize_markup


def answer_layout(message: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """The answer as segments around one group of form cards, or None when the
    message carries no flattened form copy."""
    layout = layout_message(message)
    if not layout.removed_citation_ids:
        return None
    labels = {c.get("citation_id"): citation_label(c) for c in message.get("citations") or []}
    return [
        {"text": normalize_markup(s.text), "forms": [labels.get(cid, "") for cid in s.citation_ids]}
        for s in layout.segments
    ]


def form_citation_ids(message: Dict[str, Any]) -> List[str]:
    """Citations shown as form cards, so the Sources list can skip them."""
    return list(layout_message(message).removed_citation_ids)
