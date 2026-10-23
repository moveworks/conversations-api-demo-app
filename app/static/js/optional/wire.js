/* Optional: the wire pane, a live log of every protocol frame.

   The server tags each Conversations API call and stream event with a
   "wire" frame; this module shows them in a side pane toggled from the top
   bar. With app/wire_capture.py on the server, a row that made a call
   expands to the request and response as they crossed the network, with
   credentials redacted. It is a teaching and debugging aid, not part of a
   chat surface. Delete this file and wire.css, plus the import in main.js
   and app/wire_capture.py, to drop it. */
import { postJson } from "../api.js";
import { $ } from "../dom.js";
import { hooks, useStylesheet } from "../hooks.js";

useStylesheet(new URL("./wire.css", import.meta.url));

const pane = document.createElement("aside");
pane.className = "wire-pane";
pane.hidden = true;
pane.setAttribute("aria-label", "Protocol frames");
pane.innerHTML = `
  <div class="wire-head">
    <span class="wire-title">THE WIRE</span>
    <span class="wire-sub">every frame, as it crosses</span>
    <button class="wire-clear" type="button" title="Clear frames">clear</button>
  </div>
  <ol class="wire-log"></ol>`;
document.querySelector(".workspace").appendChild(pane);
const log = pane.querySelector(".wire-log");
pane.querySelector(".wire-clear").addEventListener("click", clear);

const toggle = document.createElement("button");
toggle.className = "ghost";
toggle.type = "button";
toggle.textContent = "Wire";
toggle.title = "Show the raw protocol frames";
toggle.setAttribute("aria-pressed", "false");
toggle.addEventListener("click", () => {
  pane.hidden = !pane.hidden;
  toggle.setAttribute("aria-pressed", String(!pane.hidden));
});
$("disconnect-btn").before(toggle);

const MAX_ROWS = 300;
const MAX_EXCHANGES_PER_ROW = 20;
const PENDING_MS = 60000;

// Rows the server names by id (chat turns), and rows this page logged before
// calling the server, waiting for the exchange the call produces.
const rows = new Map();
let pending = [];
let lastRow = null;

// Capture runs while a page shows this pane: the server stops when the last
// events stream closes, so ask again on load and around each connection.
// Without app/wire_capture.py this 404s.
function startCapture() {
  postJson("/api/wire", { capture: true }).catch(() => {});
}
startCapture();
document.addEventListener("app:connected", startCapture);

hooks.wire = (frame) => {
  if (frame.attach) {
    const row = rows.get(frame.attach);
    if (row) addExchange(row, frame.exchange);
    return;
  }
  if (frame.background && frame.exchange) {
    const row = takePending(frame.exchange) || repeatOfLastRow(frame);
    if (row) {
      addExchange(row, frame.exchange);
      if (row.background) row.detail.textContent = `${frame.detail} · ${row.total} calls`;
      return;
    }
  }
  const row = addRow(frame);
  if (frame.id && frame.dir === "send") rows.set(frame.id, row);
  if (frame.exchange) addExchange(row, frame.exchange);
  else if (frame.dir === "send" && !frame.id) waitForExchange(row, frame.label);
};

function addRow(frame) {
  const li = document.createElement("li");
  const summary = document.createElement("div");
  summary.className = "wire-sum";
  const dir = document.createElement("span");
  dir.className = `dir ${frame.dir === "send" ? "send" : ""}`;
  dir.textContent = frame.dir === "send" ? "→" : "←";
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = frame.label || "";
  const detail = document.createElement("span");
  detail.className = "detail";
  detail.textContent = frame.detail || "";
  detail.hidden = !frame.detail;
  summary.append(dir, label, detail);
  li.appendChild(summary);
  if (frame.background) li.classList.add("background");
  if (frame.id) li.dataset.wireId = frame.id;
  log.appendChild(li);
  while (log.children.length > MAX_ROWS) {
    rows.delete(log.firstChild.dataset.wireId);
    log.removeChild(log.firstChild);
  }
  log.scrollTop = log.scrollHeight;
  lastRow = { li, summary, detail, label: frame.label, background: !!frame.background, exchanges: [], total: 0, panel: null };
  return lastRow;
}

function addExchange(row, exchange) {
  row.exchanges.push(exchange);
  row.total += 1;
  if (row.exchanges.length > MAX_EXCHANGES_PER_ROW) row.exchanges.shift();
  if (!row.panel) makeExpandable(row);
  if (!row.panel.hidden) renderExchanges(row);
  if (row.detail.hidden && exchange.status) {
    row.detail.textContent = String(exchange.status);
    row.detail.hidden = false;
  }
}

function makeExpandable(row) {
  row.li.classList.add("expandable");
  row.summary.tabIndex = 0;
  row.summary.setAttribute("role", "button");
  row.summary.setAttribute("aria-expanded", "false");
  row.summary.title = "Show the request and response";
  row.panel = document.createElement("div");
  row.panel.className = "wire-exchanges";
  row.panel.hidden = true;
  row.li.appendChild(row.panel);
  const toggleRow = () => {
    row.panel.hidden = !row.panel.hidden;
    row.summary.setAttribute("aria-expanded", String(!row.panel.hidden));
    if (!row.panel.hidden) renderExchanges(row);
  };
  row.summary.addEventListener("click", toggleRow);
  row.summary.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleRow();
    }
  });
}

function renderExchanges(row) {
  const parts = [];
  if (row.total > row.exchanges.length) {
    const note = document.createElement("div");
    note.className = "wire-note";
    note.textContent = `Showing the last ${row.exchanges.length} of ${row.total} calls.`;
    parts.push(note);
  }
  row.exchanges.forEach((ex, i) => parts.push(renderExchange(ex, row.exchanges.length > 1 ? i + 1 : 0)));
  row.panel.replaceChildren(...parts);
}

function renderExchange(ex, number) {
  const box = document.createElement("div");
  box.className = "wire-ex";
  const head = document.createElement("div");
  head.className = "wire-ex-head";
  const request = document.createElement("span");
  request.textContent = `${number ? `${number}. ` : ""}${ex.method} ${ex.url}`;
  const outcome = document.createElement("span");
  const failed = ex.error || !ex.status || ex.status >= 400;
  outcome.className = `wire-ex-status ${failed ? "err" : "ok"}`;
  outcome.textContent = ex.error
    ? `${ex.error} · ${ex.elapsed_ms} ms`
    : `${ex.status} ${ex.reason || ""} · ${ex.elapsed_ms} ms`;
  head.append(request, outcome);
  box.appendChild(head);
  box.appendChild(headersBlock("Request headers", ex.request_headers));
  if (ex.request_body) box.appendChild(block("Request body", ex.request_body));
  if (ex.response_headers) box.appendChild(headersBlock("Response headers", ex.response_headers));
  if (ex.response_body) box.appendChild(block(ex.streamed ? "Response stream (raw events)" : "Response body", ex.response_body));
  return box;
}

// Headers are rarely needed, so they start collapsed behind their count.
function headersBlock(title, headers) {
  const count = Object.keys(headers || {}).length;
  return block(`${title} (${count})`, headerText(headers), { collapsed: true });
}

function block(title, text, { collapsed = false } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "wire-block";
  const head = document.createElement("div");
  head.className = "wire-block-head";
  const name = document.createElement(collapsed ? "button" : "span");
  name.textContent = title;
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "wire-copy";
  copy.textContent = "copy";
  copy.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(text);
      copy.textContent = "copied";
    } catch {
      copy.textContent = "copy failed";
    }
    setTimeout(() => { copy.textContent = "copy"; }, 1500);
  });
  head.append(name, copy);
  const pre = document.createElement("pre");
  pre.textContent = text;
  if (collapsed) {
    name.type = "button";
    name.className = "wire-block-toggle";
    name.setAttribute("aria-expanded", "false");
    pre.hidden = true;
    name.addEventListener("click", (e) => {
      e.stopPropagation();
      pre.hidden = !pre.hidden;
      name.setAttribute("aria-expanded", String(!pre.hidden));
    });
  }
  wrap.append(head, pre);
  return wrap;
}

function headerText(headers) {
  return Object.entries(headers || {}).map(([name, value]) => `${name}: ${value}`).join("\n");
}

// Rows this page logs ("GET .../modals/{id}") name their call by path
// pattern; the server's exchange for that call arrives on /api/events.
function waitForExchange(row, label) {
  const match = /^([A-Z]+) (\S+)/.exec(label || "");
  if (!match) return;
  const path = match[2]
    .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    .replace(/\\\.\\\.\\\./g, ".*")
    .replace(/\\\{id\\\}/g, "[^/]+");
  pending.push({ row, pattern: new RegExp(`^${match[1]} .*${path}$`), at: Date.now() });
}

function takePending(exchange) {
  const now = Date.now();
  pending = pending.filter((p) => now - p.at < PENDING_MS && p.row.li.isConnected);
  let call = `${exchange.method} ${exchange.url}`;
  try { call = `${exchange.method} ${new URL(exchange.url).pathname}`; } catch { /* keep the raw URL */ }
  const index = pending.findIndex((p) => p.pattern.test(call));
  if (index === -1) return null;
  return pending.splice(index, 1)[0].row;
}

// Event polls repeat every few seconds; fold a repeat into the row above it.
function repeatOfLastRow(frame) {
  const last = lastRow;
  return last && last.li === log.lastElementChild && last.background && last.label === frame.label ? last : null;
}

function clear() {
  log.replaceChildren();
  rows.clear();
  pending = [];
  lastRow = null;
}

document.addEventListener("app:disconnected", () => {
  clear();
  startCapture();
});
