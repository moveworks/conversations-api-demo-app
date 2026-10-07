import { $ } from "./dom.js";
import { hooks } from "./hooks.js";
import { renderMarkdown } from "./markdown.js";
import { scrollDown } from "./scroll.js";
import { toast } from "./toast.js";
import { sendMessage } from "./chat.js";
import { fetchModalCached, openModal } from "./modals.js";

export function actionChip(action, ref) {
  if (action.type === "URL_ACTION" && action.url) {
    const a = document.createElement("a");
    a.className = "action-chip" + (action.is_auth_link ? " auth" : "");
    a.href = action.url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = action.is_auth_link ? `🔒 Connect ${action.label}` : action.label;
    if (action.is_auth_link) {
      // The assistant resumes the interrupted plugin on the user's next turn.
      a.addEventListener("click", () => {
        toast("Grant access in the new tab, then send your next message. The assistant picks up where it left off.");
      });
    }
    return a;
  }
  const btn = document.createElement("button");
  btn.className = "action-chip";
  btn.textContent = action.label;
  if (action.type === "CALLBACK_ACTION" && action.callback_id) {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("chosen")) return;
      lockActionRow(btn);
      sendMessage({ callbackId: action.callback_id, echo: action.label });
      $("chat-input").focus();
    });
  } else if (action.type === "MODAL_ACTION" && action.modal_id) {
    btn.addEventListener("click", () => openModal({ ...ref, modal_id: action.modal_id }, action.label, btn));
  } else {
    btn.disabled = true;
    btn.title = "This action type is not supported by this app.";
  }
  return btn;
}

export async function renderActions(turn, data) {
  const ref = { conversation_id: data.conversation_id, response_id: data.response_id, message_id: data.message_id };
  const group = document.createElement("div");
  group.className = "action-group";
  const cards = document.createElement("div");
  cards.className = "form-cards";
  const row = document.createElement("div");
  row.className = "actions-row";
  group.append(cards, row);
  turn.appendChild(group);

  // Chips first so the turn is usable immediately; each form offer is then
  // upgraded to a card once we know which form it opens. The label alone
  // cannot tell you: several forms arrive as identical "Complete this request"
  // buttons, named only inside the modal or by the citation a link points to.
  const chips = data.actions.map((a) => { const c = actionChip(a, ref); row.appendChild(c); return c; });
  scrollDown();

  const placeCard = (card, i) => {
    card.dataset.order = i;
    const target = hooks.formSlot(turn) || cards;
    const host = target.closest(".bubble") || group;
    hooks.enterCard(() => {
      if (host !== group) hooks.animateHeight(group, () => chips[i].remove(), { minMs: 420 });
      hooks.animateHeight(host, () => {
        if (host === group) chips[i].remove();
        const next = [...target.children].find((c) => Number(c.dataset.order) > i);
        target.insertBefore(card, next || null);
      }, { minMs: 420 });
      scrollDown(460);
    }, card, target);
  };

  // A form opened in the portal arrives already paired with its citation.
  data.actions.forEach((action, i) => {
    if (action.type === "URL_ACTION" && action.form && action.form.title) {
      placeCard(buildFormCard(chips[i].cloneNode(true), { heading: action.form.title, description: action.form.description }), i);
    }
  });

  await Promise.all(data.actions.map(async (action, i) => {
    if (action.type !== "MODAL_ACTION" || !action.modal_id) return;
    let modal;
    try { modal = await fetchModalCached({ ...ref, modal_id: action.modal_id }); } catch { return; }
    const meta = modalMeta(modal);
    // "Edit request" names itself; only offers of a distinct form become cards.
    if (!meta.heading || meta.heading.toLowerCase() === action.label.toLowerCase()) return;
    const btn = document.createElement("button");
    btn.className = "action-chip";
    btn.textContent = action.label;
    btn.addEventListener("click", () => openModal({ ...ref, modal_id: action.modal_id }, meta.heading, btn));
    placeCard(buildFormCard(btn, meta), i);
  }));
}

// The human-facing name, blurb and price, from a modal's DISPLAY fields.
function modalMeta(modal) {
  let heading = "";
  let description = "";
  let price = "";
  for (const f of modal.fields || []) {
    if (f.type !== "DISPLAY") continue;
    const t = (f.display || {}).text;
    if (!t || !t.text) continue;
    const text = t.text.replace(/\*\*/g, "").replace(/\s*\n\s*/g, " ").trim();
    if (t.style === "HEADER" && !heading) heading = text;
    else if (t.style === "SUBTLE") {
      if (/^price\s*:/i.test(text)) price = text.replace(/^price\s*:\s*/i, "");
      else if (!/^having issues\?/i.test(text) && !description) description = text;
    }
  }
  return { heading: heading || (modal.title || "").trim(), description, price };
}

function buildFormCard(button, meta) {
  const card = document.createElement("div");
  card.className = "form-card";
  const body = document.createElement("div");
  body.className = "form-card-body";
  const title = document.createElement("div");
  title.className = "form-card-title";
  title.textContent = meta.heading;
  if (meta.price) {
    const price = document.createElement("span");
    price.className = "price";
    price.textContent = meta.price;
    title.appendChild(price);
  }
  body.appendChild(title);
  if (meta.description) {
    const desc = document.createElement("div");
    desc.className = "form-card-desc";
    desc.textContent = meta.description.replace(/\s*•\s*/g, " · ");
    body.appendChild(desc);
  }
  card.append(body, button);
  return card;
}

// Once one action in a message is taken, the rest are stale: a user should
// not be able to "Edit request" something they already confirmed.
export function lockActionRow(chip) {
  const turn = chip.closest(".turn");
  if (!turn) return;
  turn.classList.add("used");
  for (const el of turn.querySelectorAll(".action-group .action-chip, .inline-form-slot .action-chip, .form-card")) {
    if (el === chip || el.contains(chip)) {
      if (el === chip) el.classList.add("chosen");
      continue;
    }
    if (el.tagName === "BUTTON") el.disabled = true;
    el.classList.add("stale");
    el.title = "Superseded: another option was chosen for this message";
  }
}

// Message.handoff: the Get Help menu, when the assistant offers one.
// The Get Help menu arrives on ordinary assistant turns, including mid-handoff
// status messages. Native surfaces show Get Help as one persistent control, so
// the menu sits behind a single button and only the newest turn offers it
// (addTurn retires older ones).
export function renderHandoff(turn, data) {
  const ref = { conversation_id: data.conversation_id, response_id: data.response_id, message_id: data.message_id };
  const wrap = document.createElement("div");
  wrap.className = "handoff";
  const toggle = document.createElement("button");
  toggle.className = "action-chip handoff-toggle";
  toggle.textContent = "Get help";
  toggle.setAttribute("aria-expanded", "false");
  const menu = document.createElement("div");
  menu.className = "handoff-menu";
  menu.hidden = true;
  toggle.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    toggle.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) scrollDown();
  });
  for (const category of data.categories) {
    const section = document.createElement("div");
    section.className = "handoff-cat";
    const h = document.createElement("h4");
    h.textContent = category.name || "Get help";
    section.appendChild(h);
    if (category.description) {
      const d = document.createElement("p");
      d.className = "cat-desc";
      d.textContent = category.description;
      section.appendChild(d);
    }
    const items = document.createElement("div");
    items.className = "handoff-items";
    for (const item of category.items) {
      if (item.action) {
        items.appendChild(actionChip({ ...item.action, label: item.title || item.action.label }, ref));
      } else if (item.text) {
        const t = document.createElement("div");
        t.className = "handoff-text";
        renderMarkdown(t, item.text);
        items.appendChild(t);
      }
    }
    section.appendChild(items);
    menu.appendChild(section);
  }
  wrap.append(toggle, menu);
  turn.appendChild(wrap);
  scrollDown();
}
