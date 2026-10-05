import { postRaw, apiDetail, readEventStream } from "./api.js";
import { $ } from "./dom.js";
import { hooks } from "./hooks.js";
import { renderMarkdown } from "./markdown.js";
import { scrollDown, stickToBottom } from "./scroll.js";
import { state } from "./state.js";
import { renderActions, renderHandoff } from "./actions.js";
import { renderFeedback } from "./feedback.js";
import { renderSources } from "./sources.js";
import { loadConversations } from "./conversations.js";

export const chatLog = $("chat-log");

// `into` lets history render older messages off-screen before prepending them.
export function addTurn(kind, into = chatLog) {
  $("chat-empty").hidden = true;
  // Only the newest turn offers Get Help (see renderHandoff).
  if (into === chatLog) chatLog.querySelectorAll(".handoff").forEach((el) => el.remove());
  const turn = document.createElement("div");
  turn.className = `turn ${kind}`;
  into.appendChild(turn);
  return turn;
}

export function addUserBubble(text, into = chatLog, files = []) {
  const turn = addTurn("user", into);
  const b = document.createElement("div");
  b.className = "bubble";
  b.textContent = text;
  for (const name of files) {
    const chip = document.createElement("div");
    chip.className = "attachment";
    chip.textContent = `📎 ${name}`;
    b.appendChild(chip);
  }
  turn.appendChild(b);
  if (into !== chatLog) return;
  stickToBottom();
  scrollDown();
}

export function addErrorLine(text) {
  const el = document.createElement("div");
  el.className = "errline";
  el.textContent = text;
  chatLog.appendChild(el);
  scrollDown();
}

export function renderAnswer(bubble, text, layout) {
  if (layout && hooks.renderAnswer) hooks.renderAnswer(bubble, layout);
  else renderMarkdown(bubble, text);
}

// One assistant or live agent message as the server presents it (live, from
// history, or from an event).
export function renderAssistantMessage(turn, message, conversationId) {
  if (message.message_id) state.shownMessageIds.add(message.message_id);
  if (message.actor === "LIVE_AGENT") renderAgentHeader(turn, message.sender);
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  renderAnswer(bubble, message.text, message.layout);
  turn.appendChild(bubble);
  renderSources(turn, message.sources, false);
  const ref = { conversation_id: conversationId, response_id: message.response_id, message_id: message.message_id };
  if (message.actions.length) renderActions(turn, { ...ref, actions: message.actions });
  if (message.handoff.length) renderHandoff(turn, { ...ref, categories: message.handoff });
  if (message.feedback.helpful || message.feedback.unhelpful) renderFeedback(turn, { ...ref, ...message.feedback });
}

// A human agent's replies arrive through the same resources as the
// assistant's; `actor` and `sender_info` are the only way to tell them apart.
function renderAgentHeader(turn, sender) {
  turn.classList.add("agent");
  const header = document.createElement("div");
  header.className = "agent-header";
  const name = (sender && sender.display_name) || "Live agent";
  if (sender && sender.avatar_url) {
    const img = document.createElement("img");
    img.className = "agent-avatar";
    img.src = sender.avatar_url;
    img.alt = "";
    img.addEventListener("error", () => img.replaceWith(initials(name)));
    header.appendChild(img);
  } else {
    header.appendChild(initials(name));
  }
  const label = document.createElement("span");
  label.className = "agent-name";
  label.textContent = name;
  const tag = document.createElement("span");
  tag.className = "agent-tag";
  tag.textContent = "Live agent";
  header.append(label, tag);
  turn.appendChild(header);
}

function initials(name) {
  const el = document.createElement("span");
  el.className = "agent-avatar initials";
  el.textContent = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  return el;
}

export async function sendMessage({ text, callbackId, echo }) {
  if (echo) addUserBubble(echo);
  $("chat-input").disabled = true;
  $("send-btn").disabled = true;

  const turn = addTurn("assistant");
  const thinking = hooks.thinking ? hooks.thinking(turn) : null;
  const ctx = { turn, thinking, bubble: null };

  try {
    const resp = await postRaw("/api/message", {
      text: text || null,
      callback_id: callbackId || null,
      conversation_id: state.conversationId,
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(apiDetail(body, `request failed (${resp.status})`));
    }
    await readEventStream(resp, (frame) => handleFrame(frame, ctx));
  } catch (e) {
    addErrorLine(String(e.message || e));
  } finally {
    if (thinking) thinking.finish();
    $("chat-input").disabled = false;
    $("send-btn").disabled = false;
    $("chat-input").focus();
  }
}

function handleFrame({ event, data }, ctx) {
  if (event === "wire") return hooks.wire(data);
  if (event === "conversation") {
    state.conversationId = data.conversation_id;
    loadConversations();
    return;
  }
  if (event === "delta") {
    if (data.output_type === "REASONING_MESSAGE") {
      if (ctx.thinking) ctx.thinking.update(data.text || "");
      scrollDown();
    } else if (data.output_type === "MESSAGE") {
      if (!ctx.bubble) {
        if (ctx.thinking) ctx.thinking.settle();
        ctx.bubble = document.createElement("div");
        ctx.bubble.className = "bubble";
        ctx.turn.appendChild(ctx.bubble);
        hooks.fadeIn(ctx.bubble);
      }
      hooks.animateHeight(ctx.bubble, () => renderAnswer(ctx.bubble, data.text || "", data.layout));
      renderSources(ctx.turn, data.sources, true);
      scrollDown();
    }
    return;
  }
  if (event === "completed") {
    if (data.status === "FAILED") addErrorLine("The assistant could not complete that response. Try again, or rephrase.");
    return;
  }
  if (event === "late_message") {
    if (state.shownMessageIds.has(data.message.message_id)) return;
    // A reply the API filed under an earlier response (the Exit Live Chat
    // farewell): fill this turn if it is still empty, else add a turn.
    const turn = ctx.bubble ? addTurn("assistant") : ctx.turn;
    if (!ctx.bubble && ctx.thinking) ctx.thinking.settle();
    renderAssistantMessage(turn, data.message, data.conversation_id);
    ctx.bubble = ctx.bubble || turn.querySelector(".bubble");
    hooks.reveal(turn.lastElementChild);
    scrollDown();
    return;
  }
  const render = { actions: renderActions, handoff: renderHandoff, feedback_ids: renderFeedback }[event];
  if (data.message_id) state.shownMessageIds.add(data.message_id);
  if (render) {
    const before = ctx.turn.lastElementChild;
    render(ctx.turn, data);
    const added = ctx.turn.lastElementChild;
    if (added && added !== before) hooks.reveal(added);
    scrollDown();
    return;
  }
  if (event === "error") addErrorLine(data.message || "Something went wrong.");
}
