/* The side panel (a tab strip on the right of the workspace) and the
   conversations sidebar on narrow screens.

   Notifications is a core tab declared in index.html; optional modules add
   their own with addRailTab. The panel sits in the layout, below the top bar,
   so opening it narrows the chat instead of covering anything. On narrow
   screens the panel and the sidebar overlay the chat behind a scrim. */
import { $, emit } from "./dom.js";

const rail = $("rail");
const tabs = $("rail-tabs");
const scrim = $("scrim");
const sidebar = $("conv-sidebar");
const narrow = window.matchMedia("(max-width: 1000px)");

const WIDTH_KEY = "mw_capi_rail_width";
const MIN_WIDTH = 320;
const CHAT_MIN_REM = 24; // the chat column keeps at least this much room

let active = null;
let returnFocus = null;

// panel: an element with role=tabpanel, appended to the side panel.
export function addRailTab(id, label, panel) {
  const tab = document.createElement("button");
  tab.type = "button";
  tab.className = "rail-tab";
  tab.id = `tab-${id}`;
  tab.dataset.tab = id;
  tab.setAttribute("role", "tab");
  tab.setAttribute("aria-selected", "false");
  tab.textContent = label;
  panel.id = panel.id || `panel-${id}`;
  panel.classList.add("rail-panel");
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", tab.id);
  panel.hidden = true;
  tab.setAttribute("aria-controls", panel.id);
  tabs.appendChild(tab);
  rail.appendChild(panel);
  return tab;
}

export function railShowing(id) {
  return !rail.hidden && active === id;
}

export function openRail(id) {
  if (rail.hidden) returnFocus = document.activeElement;
  rail.hidden = false;
  select(id);
  updateScrim();
  $(`tab-${id}`)?.focus({ preventScroll: true });
}

export function closeRail() {
  if (rail.hidden) return;
  rail.hidden = true;
  updateScrim();
  emit("rail:change", { active: null });
  if (returnFocus && returnFocus.isConnected) returnFocus.focus({ preventScroll: true });
  returnFocus = null;
}

export function toggleRail(id) {
  if (railShowing(id)) closeRail();
  else openRail(id);
}

function select(id) {
  active = id;
  for (const tab of tabs.querySelectorAll(".rail-tab")) {
    const on = tab.dataset.tab === id;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(tab.getAttribute("aria-controls")).hidden = !on;
  }
  emit("rail:change", { active: id });
}

tabs.addEventListener("click", (e) => {
  const tab = e.target.closest(".rail-tab");
  if (tab) select(tab.dataset.tab);
});
tabs.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const all = [...tabs.querySelectorAll(".rail-tab")];
  const next = all[(all.findIndex((t) => t.dataset.tab === active) + (e.key === "ArrowRight" ? 1 : all.length - 1)) % all.length];
  select(next.dataset.tab);
  next.focus();
});
$("rail-close").addEventListener("click", closeRail);

// ---------- width: drag or arrow keys on the left edge, kept across reloads ----------

function setWidth(px) {
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const sidebarWidth = narrow.matches || sidebar.hidden ? 0 : sidebar.getBoundingClientRect().width;
  const max = Math.max(MIN_WIDTH, window.innerWidth - sidebarWidth - CHAT_MIN_REM * rem);
  const width = Math.round(Math.min(max, Math.max(MIN_WIDTH, px)));
  rail.style.setProperty("--rail-width", `${width}px`);
  return width;
}
const saved = Number(localStorage.getItem(WIDTH_KEY));
if (saved) setWidth(saved);
// A narrower window re-clamps the chosen width; the saved choice is kept.
window.addEventListener("resize", () => {
  const chosen = Number(localStorage.getItem(WIDTH_KEY));
  if (chosen) setWidth(chosen);
});

const handle = $("rail-resize");
handle.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  handle.setPointerCapture(e.pointerId);
  document.body.classList.add("resizing");
  const move = (ev) => setWidth(rail.getBoundingClientRect().right - ev.clientX);
  // Fires on pointerup and on any interrupted drag, so cleanup always runs.
  const done = () => {
    handle.removeEventListener("pointermove", move);
    document.body.classList.remove("resizing");
    localStorage.setItem(WIDTH_KEY, String(Math.round(rail.getBoundingClientRect().width)));
  };
  handle.addEventListener("pointermove", move);
  handle.addEventListener("lostpointercapture", done, { once: true });
});
handle.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  e.preventDefault();
  const width = setWidth(rail.getBoundingClientRect().width + (e.key === "ArrowLeft" ? 32 : -32));
  localStorage.setItem(WIDTH_KEY, String(width));
});

// ---------- narrow screens: sidebar and panel overlay the chat ----------

export function setSidebarOpen(open) {
  sidebar.hidden = !open;
  $("conv-toggle").setAttribute("aria-expanded", String(open));
  updateScrim();
}

// Only overlays need closing after a pick; on wide screens the sidebar stays.
export function closeSidebarIfOverlay() {
  if (narrow.matches) setSidebarOpen(false);
}

function updateScrim() {
  scrim.hidden = !narrow.matches || (sidebar.hidden && rail.hidden);
}

function applyBreakpoint() {
  setSidebarOpen(!narrow.matches);
}
narrow.addEventListener("change", applyBreakpoint);
applyBreakpoint();

$("conv-toggle").addEventListener("click", () => setSidebarOpen(sidebar.hidden));
scrim.addEventListener("click", () => {
  if (narrow.matches) setSidebarOpen(false);
  closeRail();
});

// Escape closes the topmost overlay: a modal handles its own first.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !$("modal-backdrop").hidden) return;
  if (!rail.hidden) closeRail();
  else if (narrow.matches && !sidebar.hidden) {
    setSidebarOpen(false);
    $("conv-toggle").focus();
  }
});
