import { $ } from "./dom.js";
import { hooks } from "./hooks.js";
import { renderMarkdown } from "./markdown.js";
import { scrollDown } from "./scroll.js";
import { state } from "./state.js";
import { addTurn, renderAssistantMessage } from "./chat.js";
import { openConversation } from "./conversations.js";

let source = null;
let unread = 0;

export function connectNotifications() {
  closeNotifications();
  source = new EventSource("/api/events");
  source.addEventListener("notification", (ev) => {
    const data = JSON.parse(ev.data);
    // Async follow-ups (ticket confirmations, live agent updates) arrive as
    // events on the SAME conversation after the response already completed.
    // Those belong in the thread; only other conversations' events are
    // notifications.
    if (data.conversation_id && data.conversation_id === state.conversationId) {
      if (data.message?.message_id && state.shownMessageIds.has(data.message.message_id)) return;
      const turn = addTurn("assistant");
      if (data.message) {
        renderAssistantMessage(turn, data.message, data.conversation_id);
      } else {
        const bubble = document.createElement("div");
        bubble.className = "bubble";
        renderMarkdown(bubble, data.text || "");
        turn.appendChild(bubble);
      }
      hooks.notificationArrived(data, turn);
      scrollDown();
      hooks.wire({ dir: "recv", label: "event → chat (same conversation)", detail: (data.text || "").slice(0, 80) });
      return;
    }
    $("notif-empty").hidden = true;
    const item = document.createElement("div");
    item.className = "notif-item";
    const body = document.createElement("div");
    renderMarkdown(body, data.text || "(no text)");
    const meta = document.createElement("div");
    meta.className = "meta";
    const author = data.actor === "LIVE_AGENT" ? (data.message?.sender?.display_name || "Live agent") : (data.actor || "");
    meta.textContent = `${data.created_at || ""} · ${author}`;
    item.append(body, meta);
    if (data.conversation_id) {
      item.classList.add("clickable");
      item.title = "Open this conversation";
      item.addEventListener("click", () => { $("notif-drawer").hidden = true; openConversation(data.conversation_id); });
    }
    $("notif-list").prepend(item);
    hooks.notificationArrived(data, item);
    hooks.wire({ dir: "recv", label: "event (poll)", detail: (data.text || "").slice(0, 80) });
    if ($("notif-drawer").hidden) {
      unread += 1;
      $("notif-badge").textContent = String(unread);
      $("notif-badge").hidden = false;
    }
  });
}

export function closeNotifications() {
  if (source) source.close();
  source = null;
  unread = 0;
  $("notif-badge").hidden = true;
}

export function toggleNotifications() {
  const drawer = $("notif-drawer");
  drawer.hidden = !drawer.hidden;
  if (!drawer.hidden) {
    unread = 0;
    $("notif-badge").hidden = true;
  }
}
