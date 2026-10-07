/* Moveworks Conversations API: demo app. Plain ES modules, no build step.

   Optional modules: each import below adds one behavior on top of the core
   app. Delete the import (and the files) to drop it; the app keeps working.
     motion        animated layout changes instead of snapping
     thinking      the reasoning trail (pairs with server app/streaming.py)
     inline-forms  form cards inside the answer (pairs with app/inline_forms.py)
     wire          a pane showing every protocol frame as it crosses
     test-notify   send yourself a proactive notification (pairs with app/test_notifications.py) */
import "./optional/motion.js";
import "./optional/thinking.js";
import "./optional/inline-forms.js";
import "./optional/wire.js";
import "./optional/test-notify.js";

import { $ } from "./dom.js";
import { boot, connect, disconnect } from "./connect.js";
import { sendMessage } from "./chat.js";
import { startNewConversation, toggleArchivedView } from "./conversations.js";
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
  if (!text) return;
  input.value = "";
  autosizeComposer();
  sendMessage({ text, echo: text });
  input.focus();
});

function applyTheme(mode) {
  document.documentElement.dataset.theme = mode;
  localStorage.setItem("mw_capi_theme", mode);
}
$("theme-btn").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});
applyTheme(localStorage.getItem("mw_capi_theme") ||
  (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

$("connect-btn").addEventListener("click", connect);
$("api-key").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
$("disconnect-btn").addEventListener("click", disconnect);
$("new-conv-btn").addEventListener("click", startNewConversation);
$("new-conv-btn2").addEventListener("click", startNewConversation);
$("conv-archived-toggle").addEventListener("click", toggleArchivedView);
$("conv-toggle").addEventListener("click", () => ($("conv-sidebar").hidden = !$("conv-sidebar").hidden));
$("notif-btn").addEventListener("click", toggleNotifications);
$("notif-close").addEventListener("click", () => ($("notif-drawer").hidden = true));
$("modal-close").addEventListener("click", closeModal);
$("modal-submit").addEventListener("click", (e) => { e.preventDefault(); submitModal(); });
$("modal-backdrop").addEventListener("click", (e) => { if (e.target === $("modal-backdrop")) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("modal-backdrop").hidden) closeModal(); });

boot();
