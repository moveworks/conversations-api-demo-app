/* Moveworks Conversations API: demo app. Plain ES modules, no build step.

   Optional modules: each import below adds one behavior on top of the core
   app. Delete the import (and the files) to drop it; the app keeps working.
     motion        animated layout changes instead of snapping
     thinking      the reasoning trail (pairs with server app/streaming.py)
     inline-forms  form cards inside the answer (pairs with app/inline_forms.py)
     wire          a side panel tab showing every protocol frame as it crosses (pairs with app/wire_capture.py)
     attachments   attach files to messages and modal forms (pairs with app/attachments.py)
     test-notify   send yourself a proactive notification (pairs with app/test_notifications.py) */
import "./optional/motion.js";
import "./optional/thinking.js";
import "./optional/inline-forms.js";
import "./optional/wire.js";
import "./optional/attachments.js";
import "./optional/test-notify.js";

import { $ } from "./dom.js";
import { hooks } from "./hooks.js";
import "./layout.js";
import { toast } from "./toast.js";
import { boot, connect, disconnect } from "./connect.js";
import { sendMessage } from "./chat.js";
import { clearConversationList, showAllConversations, startNewConversation, toggleArchivedView } from "./conversations.js";
import { closeModal, submitModal } from "./modals.js";
import { toggleNotifications } from "./notifications.js";

const input = $("chat-input");

// Grow with the text; scroll only once the box reaches its cap. scrollHeight
// excludes the border, so add it back or the box always overflows by 2px.
function autosizeComposer() {
  input.style.height = "auto";
  const border = input.offsetHeight - input.clientHeight;
  const cap = window.innerHeight * 0.6;
  const needed = input.scrollHeight + border;
  input.style.height = Math.min(needed, cap) + "px";
  input.style.overflowY = needed > cap ? "auto" : "hidden";
}
// The chat is hidden behind the connect screen at load, where it measures as zero.
document.addEventListener("app:connected", autosizeComposer);

input.addEventListener("input", autosizeComposer);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  const { files, busy } = hooks.composerFiles();
  if (busy) return toast("Waiting for uploads to finish.");
  if (!text && !files.length) return;
  input.value = "";
  autosizeComposer();
  hooks.clearComposerFiles();
  sendMessage({ text, echo: text, files });
  input.focus();
});

function applyTheme(mode) {
  document.documentElement.dataset.theme = mode;
  localStorage.setItem("mw_capi_theme", mode);
  $("theme-btn").textContent = mode === "dark" ? "Light mode" : "Dark mode";
}
$("theme-btn").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

// ---------- the top bar's More menu ----------
const moreBtn = $("more-btn");
const moreMenu = $("more-menu");
function setMenuOpen(open) {
  moreMenu.hidden = !open;
  moreBtn.setAttribute("aria-expanded", String(open));
  if (open) moreMenu.querySelector("button").focus();
}
moreBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  setMenuOpen(moreMenu.hidden);
});
moreMenu.addEventListener("click", () => {
  setMenuOpen(false);
  moreBtn.focus();
});
moreMenu.addEventListener("focusout", (e) => {
  if (!moreMenu.hidden && !moreMenu.contains(e.relatedTarget) && e.relatedTarget !== moreBtn) setMenuOpen(false);
});
moreMenu.addEventListener("keydown", (e) => {
  const items = [...moreMenu.querySelectorAll("button")];
  const i = items.indexOf(document.activeElement);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
  } else if (e.key === "Escape") {
    e.stopPropagation();
    setMenuOpen(false);
    moreBtn.focus();
  }
});
document.addEventListener("click", (e) => { if (!moreMenu.hidden && !moreMenu.contains(e.target)) setMenuOpen(false); });

for (const chip of $("starter-prompts").querySelectorAll("button")) {
  chip.addEventListener("click", () => {
    const text = chip.textContent.trim();
    sendMessage({ text, echo: text });
    input.focus();
  });
}
applyTheme(localStorage.getItem("mw_capi_theme") ||
  (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

$("connect-btn").addEventListener("click", connect);
$("api-key").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
$("disconnect-btn").addEventListener("click", disconnect);
$("new-conv-btn").addEventListener("click", startNewConversation);
$("conv-archived-toggle").addEventListener("click", toggleArchivedView);
$("conv-clear").addEventListener("click", clearConversationList);
$("conv-show-all").addEventListener("click", showAllConversations);
$("notif-btn").addEventListener("click", toggleNotifications);
$("modal-close").addEventListener("click", closeModal);
$("modal-cancel").addEventListener("click", closeModal);
$("modal-submit").addEventListener("click", (e) => { e.preventDefault(); submitModal(); });
$("modal-backdrop").addEventListener("click", (e) => { if (e.target === $("modal-backdrop")) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("modal-backdrop").hidden) closeModal(); });

boot();
