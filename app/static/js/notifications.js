import { $ } from "./dom.js";
import { hooks } from "./hooks.js";
import { renderMarkdown } from "./markdown.js";
import { scrollDown } from "./scroll.js";
import { state } from "./state.js";
import { addStatusLine, addTurn, renderAssistantMessage } from "./chat.js";
import { openConversation } from "./conversations.js";
import { closeRail, railShowing, toggleRail } from "./layout.js";

let source = null;
let unread = 0;
// One consent can produce several AUTHORIZE events; the first marks completion.
const authorizedConversations = new Set();

export function connectNotifications() {
  closeNotifications();
  source = new EventSource("/api/events");
  setConnection("connecting");
  // EventSource reconnects on its own; readyState says whether it still is.
  source.addEventListener("open", () => setConnection("live"));
  source.addEventListener("error", () => setConnection(source?.readyState === EventSource.CLOSED ? "down" : "reconnecting"));
  // Calls made outside a chat turn, from the server's optional wire capture.
  source.addEventListener("wire", (ev) => hooks.wire(JSON.parse(ev.data)));
  source.addEventListener("notification", (ev) => {
    const data = JSON.parse(ev.data);
    // Async follow-ups (ticket confirmations, live agent updates) arrive as
    // events on the SAME conversation after the response already completed.
    // Those belong in the thread; only other conversations' events are
    // notifications.
    const status = statusText(data);
    if (data.conversation_id && data.conversation_id === state.conversationId) {
      if (!data.message) {
        if (!status) return;
        const line = addStatusLine(status);
        hooks.notificationArrived(data, line);
        hooks.wire({ dir: "recv", label: "event → chat (same conversation)", detail: status });
        return;
      }
      if (data.message.message_id && state.shownMessageIds.has(data.message.message_id)) return;
      const turn = addTurn("assistant");
      renderAssistantMessage(turn, data.message, data.conversation_id);
      hooks.notificationArrived(data, turn);
      // Follow-ups land in the thread without a reply in flight, so announce them here.
      const spoken = turn.querySelector(".bubble")?.textContent || "";
      scrollDown();
      if (status) addStatusLine(status, { announce: false });
      $("reply-status").textContent = [spoken, status].filter(Boolean).join(" ");
      hooks.wire({ dir: "recv", label: "event → chat (same conversation)", detail: (data.text || "").slice(0, 80) });
      return;
    }
    $("notif-empty").hidden = true;
    const item = document.createElement("div");
    item.className = "notif-item";
    const body = document.createElement("div");
    renderMarkdown(body, data.text || status || "(no text)");
    const meta = document.createElement("div");
    meta.className = "meta";
    const author = data.actor === "LIVE_AGENT" ? (data.message?.sender?.display_name || "Live agent") : ACTORS[data.actor] || "";
    meta.textContent = [formatTime(data.created_at), author, data.message ? status : ""].filter(Boolean).join(" · ");
    item.append(body, meta);
    if (data.conversation_id) {
      // The body is rendered markdown and can hold links, so the item itself
      // is not a button: a real one opens the conversation, and a click
      // anywhere else on the item does the same for mouse users.
      item.classList.add("clickable");
      const open = () => { closeRail(); openConversation(data.conversation_id); };
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "link-btn notif-open";
      btn.textContent = "Open conversation";
      btn.addEventListener("click", (e) => { e.stopPropagation(); open(); });
      meta.append(" · ", btn);
      item.addEventListener("click", (e) => { if (!e.target.closest("a, button")) open(); });
    }
    $("notif-list").prepend(item);
    hooks.notificationArrived(data, item);
    hooks.wire({ dir: "recv", label: "event (poll)", detail: (data.text || status).slice(0, 80) });
    if (!railShowing("notifications")) {
      unread += 1;
      $("notif-badge").textContent = String(unread);
      $("notif-badge").hidden = false;
    }
  });
}

// Read from the event's category details, never from the message text.
function statusText(data) {
  if (data.live_agent_session_state === "ENDED") return "Live agent chat ended";
  if (data.category !== "USER_CONSENT_AUTH") return "";
  if (data.consent_operation_type === "AUTHORIZE") {
    if (authorizedConversations.has(data.conversation_id)) return "";
    authorizedConversations.add(data.conversation_id);
    return "Account connected";
  }
  if (data.consent_operation_type === "REVOKE") {
    authorizedConversations.delete(data.conversation_id);
    return "Account disconnected";
  }
  return "";
}

export function closeNotifications() {
  if (source) source.close();
  source = null;
  unread = 0;
  authorizedConversations.clear();
  $("notif-badge").hidden = true;
  setConnection("connecting");
}

export function toggleNotifications() {
  toggleRail("notifications");
}

document.addEventListener("rail:change", (e) => {
  const showing = e.detail.active === "notifications";
  $("notif-btn").setAttribute("aria-pressed", String(showing));
  if (showing) {
    unread = 0;
    $("notif-badge").hidden = true;
  }
});

const ACTORS = { ASSISTANT: "Assistant", USER: "You" };

const CONNECTION = {
  connecting: "Connecting to the events stream",
  live: "Connected: notifications and follow-ups arrive live",
  reconnecting: "Lost the events stream; reconnecting",
  down: "Disconnected from the events stream. Reload the page.",
};

function setConnection(state) {
  const dot = $("status-dot");
  dot.dataset.state = state;
  dot.title = CONNECTION[state];
  // Announce changes after the first connect, not the routine connect itself.
  $("conn-label").textContent = state === "live" && !dot.dataset.seen ? "" : CONNECTION[state];
  if (state === "live") dot.dataset.seen = "1";
}

function formatTime(iso) {
  const when = new Date(iso || "");
  if (Number.isNaN(when.getTime())) return "";
  const sameDay = when.toDateString() === new Date().toDateString();
  return sameDay
    ? when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : when.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
