import { postJson } from "./api.js";
import { hooks } from "./hooks.js";
import { scrollDown } from "./scroll.js";
import { toast } from "./toast.js";

export function renderFeedback(turn, data) {
  const row = document.createElement("div");
  row.className = "feedback-row";
  for (const [kind, icon, label] of [["helpful", "👍", "Helpful"], ["unhelpful", "👎", "Not helpful"]]) {
    if (!data[kind]) continue;
    const btn = document.createElement("button");
    btn.className = "fb-btn";
    btn.textContent = icon;
    btn.title = label;
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
  if (row.querySelector(".fb-comment")) return;
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
  box.addEventListener("submit", (e) => {
    e.preventDefault();
    submit(row, btn, data, "unhelpful", input.value).then((ok) => { if (ok) box.remove(); });
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
    hooks.wire({ dir: "send", label: "POST .../feedback", detail: comment.trim() ? `${kind} + comment` : kind });
    toast(kind === "helpful" ? "Thanks, feedback recorded." : "Feedback recorded.");
    return true;
  } catch (e) {
    row.querySelectorAll("button").forEach((b) => (b.disabled = false));
    toast(String(e.message || e));
    return false;
  }
}
