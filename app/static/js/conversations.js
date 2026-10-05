import { getJson, apiDetail } from "./api.js";
import { $ } from "./dom.js";
import { hooks } from "./hooks.js";
import { scrollDown, stickToBottom } from "./scroll.js";
import { state } from "./state.js";
import { toast } from "./toast.js";
import { addTurn, addUserBubble, chatLog, renderAssistantMessage } from "./chat.js";

const PAGE = 20;
let showArchived = false;

export async function loadConversations() {
  try {
    // ListConversations returns `title`, so each page of the sidebar costs one request.
    const body = await getJson(`/api/conversations?limit=${PAGE}&archived=${showArchived}`);
    const list = $("conv-list");
    list.replaceChildren();
    const conversations = body.conversations || [];
    if (!conversations.length) {
      const li = document.createElement("li");
      li.className = "conv-empty";
      li.textContent = showArchived ? "No archived conversations." : "No conversations yet. Send a message to start one.";
      list.appendChild(li);
      return;
    }
    appendConversations(list, conversations, body.metadata?.next_cursor);
  } catch { /* the sidebar is best-effort */ }
}

function appendConversations(list, conversations, nextCursor) {
  for (const c of conversations) list.appendChild(conversationItem(c));
  if (!nextCursor) return;
  const li = document.createElement("li");
  li.className = "conv-more";
  const btn = document.createElement("button");
  btn.className = "ghost small";
  btn.textContent = "Load more";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const body = await getJson(`/api/conversations?limit=${PAGE}&archived=${showArchived}&cursor=${encodeURIComponent(nextCursor)}`);
      li.remove();
      appendConversations(list, body.conversations || [], body.metadata?.next_cursor);
    } catch (e) {
      btn.disabled = false;
      toast(String(e.message || e));
    }
  });
  li.appendChild(btn);
  list.appendChild(li);
}

function conversationItem(c) {
  const li = document.createElement("li");
  li.className = "conv-item" + (c.conversation_id === state.conversationId ? " active" : "");
  li.dataset.id = c.conversation_id;
  li.title = c.conversation_id;
  const preview = document.createElement("div");
  preview.className = "conv-preview";
  preview.textContent = (c.title || "").trim() || "New conversation";
  const meta = document.createElement("div");
  meta.className = "conv-meta";
  meta.textContent = relTime(c.updated_at || c.created_at);
  const menu = document.createElement("button");
  menu.className = "conv-menu-btn";
  menu.textContent = "⋯";
  menu.title = "Rename or archive";
  menu.setAttribute("aria-label", "Conversation options");
  menu.addEventListener("click", (e) => { e.stopPropagation(); openItemMenu(li, c, preview); });
  li.append(preview, meta, menu);
  li.addEventListener("click", () => { if (!li.classList.contains("editing")) openConversation(c.conversation_id); });
  return li;
}

function openItemMenu(li, c, preview) {
  closeItemMenus();
  const pop = document.createElement("div");
  pop.className = "conv-menu";
  const item = (label, fn) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.addEventListener("click", (e) => { e.stopPropagation(); pop.remove(); fn(); });
    pop.appendChild(b);
  };
  item("Rename", () => startRename(li, c, preview));
  item(showArchived ? "Unarchive" : "Archive", () => setArchived(li, c, !showArchived));
  li.appendChild(pop);
  setTimeout(() => document.addEventListener("click", closeItemMenus, { once: true }));
}

function closeItemMenus() {
  document.querySelectorAll(".conv-menu").forEach((m) => m.remove());
}

function startRename(li, c, preview) {
  li.classList.add("editing");
  const input = document.createElement("input");
  input.className = "conv-rename";
  input.value = preview.textContent;
  input.maxLength = 200;
  preview.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const title = input.value.trim();
    if (save && title && title !== preview.textContent) {
      try {
        await patchConversation(c.conversation_id, { title });
        preview.textContent = title;
        c.title = title;
      } catch (e) {
        toast(String(e.message || e));
      }
    }
    input.replaceWith(preview);
    li.classList.remove("editing");
  };
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

async function setArchived(li, c, archived) {
  try {
    await patchConversation(c.conversation_id, { archived });
    li.remove();
    toast(archived ? "Archived." : "Moved back to conversations.");
    if (!$("conv-list").querySelector(".conv-item")) loadConversations();
  } catch (e) {
    toast(String(e.message || e));
  }
}

async function patchConversation(id, body) {
  hooks.wire({ dir: "send", label: "PATCH /conversations/{id}", detail: JSON.stringify(body) });
  const resp = await fetch(`/api/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(apiDetail(data, `update failed (${resp.status})`));
  return data;
}

export function toggleArchivedView() {
  showArchived = !showArchived;
  const btn = $("conv-archived-toggle");
  btn.setAttribute("aria-pressed", String(showArchived));
  btn.classList.toggle("on", showArchived);
  btn.textContent = showArchived ? "Back" : "Archived";
  btn.title = showArchived ? "Back to conversations" : "Show archived conversations";
  $("conv-heading").textContent = showArchived ? "ARCHIVED" : "CHATS";
  loadConversations();
}

function relTime(iso) {
  if (!iso) return "";
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 90) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

function markActiveConversation() {
  document.querySelectorAll(".conv-item").forEach((li) => {
    li.classList.toggle("active", li.dataset.id === state.conversationId);
  });
}

// History records a button press without its label, so it reads as a note, not a bubble.
function addSysline(text, into) {
  const el = document.createElement("div");
  el.className = "sysline";
  el.textContent = text;
  into.appendChild(el);
}

function renderMessages(messages, id, into) {
  for (const m of messages) {
    if (m.actor === "USER" && m.is_action) addSysline("Selected an option", into);
    else if (m.actor === "USER") addUserBubble(m.text, into, m.files || []);
    else renderAssistantMessage(addTurn("assistant", into), m, id);
  }
}

export async function openConversation(id) {
  state.conversationId = id;
  markActiveConversation();
  if (window.innerWidth <= 1000) $("conv-sidebar").hidden = true;
  chatLog.replaceChildren();
  $("chat-empty").hidden = true;
  const loading = document.createElement("div");
  loading.className = "sysline";
  loading.textContent = "Loading conversation…";
  chatLog.appendChild(loading);
  hooks.wire({ dir: "send", label: "GET /conversations/{id}/messages" });
  try {
    const { messages, next_cursor: nextCursor } = await getJson(`/api/conversations/${id}/messages`);
    hooks.wire({ dir: "recv", label: `messages x${messages.length}` });
    loading.remove();
    renderMessages(messages, id, chatLog);
    if (nextCursor) chatLog.prepend(olderButton(id, nextCursor));
    stickToBottom();
    scrollDown();
  } catch (e) {
    loading.replaceChildren();
    loading.className = "errline";
    loading.append(`Could not load this conversation. ${e.message || e} `);
    const retry = document.createElement("button");
    retry.className = "action-chip";
    retry.textContent = "Try again";
    retry.addEventListener("click", () => openConversation(id));
    loading.appendChild(retry);
  }
}

// History pages newest first; each older page goes above what is shown,
// keeping the reader's place.
function olderButton(id, cursor) {
  const wrap = document.createElement("div");
  wrap.className = "load-older";
  const btn = document.createElement("button");
  btn.className = "ghost small";
  btn.textContent = "Load older messages";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const { messages, next_cursor: nextCursor } = await getJson(`/api/conversations/${id}/messages?cursor=${encodeURIComponent(cursor)}`);
      if (state.conversationId !== id) return;
      const older = document.createDocumentFragment();
      const holder = document.createElement("div");
      renderMessages(messages, id, holder);
      holder.querySelectorAll(".handoff").forEach((el) => el.remove());
      older.append(...holder.childNodes);
      if (nextCursor) older.prepend(olderButton(id, nextCursor));
      const scroller = $("chat-scroll");
      const fromBottom = scroller.scrollHeight - scroller.scrollTop;
      wrap.replaceWith(older);
      scroller.scrollTop = scroller.scrollHeight - fromBottom;
    } catch (e) {
      btn.disabled = false;
      toast(String(e.message || e));
    }
  });
  wrap.appendChild(btn);
  return wrap;
}

export function startNewConversation() {
  state.conversationId = null;
  markActiveConversation();
  chatLog.replaceChildren();
  $("chat-empty").hidden = false;
  $("chat-input").focus();
}
