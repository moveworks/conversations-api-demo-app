import { postJson } from "./api.js";
import { hooks } from "./hooks.js";
import { scrollDown } from "./scroll.js";
import { toast } from "./toast.js";

const THUMB = "M7 10v11H4a1 1 0 01-1-1v-9a1 1 0 011-1h3zm0 0l4-7a2.5 2.5 0 012.5 2.5V9h5.2a2 2 0 012 2.3l-1.2 7.5a2 2 0 01-2 1.7H7";
const ICONS = {
  helpful: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" aria-hidden="true"><path d="${THUMB}"/></svg>`,
  unhelpful: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" aria-hidden="true" style="transform: scaleY(-1)"><path d="${THUMB}"/></svg>`,
};

export function renderFeedback(turn, data) {
  const row = document.createElement("div");
  row.className = "feedback-row";
  for (const [kind, label] of [["helpful", "Helpful"], ["unhelpful", "Not helpful"]]) {
    if (!data[kind]) continue;
    const btn = document.createElement("button");
    btn.className = "fb-btn";
    btn.innerHTML = ICONS[kind];
    btn.title = label;
    btn.setAttribute("aria-label", label);
    btn.addEventListener("click", () => {
      // A thumbs-down is where a reason helps most; the API takes it as
      // `additional_feedback` on the same call.
      if (kind === "unhelpful") openComment(row, btn, data);
      else submit(row, btn, data, kind, "");
    });
    row.appendChild(btn);
  }
  if (row.children.length) turn.appendChild(row);
}

function openComment(row, btn, data) {
  // The box is the row's next sibling, not a child.
  if (row.nextElementSibling?.classList.contains("fb-comment")) {
    row.nextElementSibling.querySelector("textarea")?.focus();
    return;
  }
  const box = document.createElement("form");
  box.className = "fb-comment";
  const input = document.createElement("textarea");
  input.rows = 2;
  input.maxLength = 2000;
  input.placeholder = "What was wrong? (optional)";
  const send = document.createElement("button");
  send.type = "submit";
  send.className = "action-chip";
  send.textContent = "Send feedback";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "action-chip subtle";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => box.remove());
  box.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (send.disabled) return; // a second Enter while the first send is in flight
    send.disabled = cancel.disabled = true;
    if (await submit(row, btn, data, "unhelpful", input.value)) box.remove();
    else send.disabled = cancel.disabled = false;
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); box.requestSubmit(); }
  });
  const actions = document.createElement("div");
  actions.className = "fb-comment-actions";
  actions.append(send, cancel);
  box.append(input, actions);
  row.after(box);
  input.focus();
  scrollDown();
}

async function submit(row, btn, data, kind, comment) {
  row.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    await postJson("/api/feedback", {
      conversation_id: data.conversation_id,
      response_id: data.response_id,
      message_id: data.message_id,
      callback_id: data[kind],
      additional_feedback: comment.trim() || null,
    }, "feedback failed");
    btn.classList.add("sent");
    // A vote from either button settles the message, so close an open comment box.
    if (row.nextElementSibling?.classList.contains("fb-comment")) row.nextElementSibling.remove();
    hooks.wire({ dir: "send", label: "POST .../feedback", detail: comment.trim() ? `${kind} + comment` : kind });
    toast(kind === "helpful" ? "Thanks, feedback recorded." : "Feedback recorded.");
    return true;
  } catch (e) {
    row.querySelectorAll("button").forEach((b) => (b.disabled = false));
    toast(String(e.message || e));
    return false;
  }
}
