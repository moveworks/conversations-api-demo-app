/* Optional: the Wire tab, a live log of every protocol frame.

   The server tags each Conversations API call and stream event with a
   "wire" frame; this module shows them on a Wire tab in the side panel,
   toggled from the top bar. With app/wire_capture.py on the server, a row
   that made a call expands to the request and response as they crossed the
   network, with credentials redacted. It is a teaching and debugging aid,
   not part of a chat surface. Delete this file and wire.css, plus the import
   in main.js and app/wire_capture.py, to drop it. */
import { postJson } from "../api.js";
import { $ } from "../dom.js";
import { hooks, useStylesheet } from "../hooks.js";
import { addRailTab, toggleRail } from "../layout.js";

useStylesheet(new URL("./wire.css", import.meta.url));

const panel = document.createElement("section");
panel.className = "wire-pane";
panel.innerHTML = `
  <div class="wire-tools">
    <button class="wire-opt" type="button" data-opt="hidePolls" aria-pressed="true" title="Hide the repeating event polls">Hide polls</button>
    <button class="wire-opt" type="button" data-opt="follow" aria-pressed="true" title="Scroll to new frames as they arrive">Follow</button>
    <span class="wire-spacer"></span>
    <button class="wire-clear" type="button" title="Clear frames">Clear</button>
  </div>
  <ol class="wire-log hide-polls" aria-label="Protocol frames"></ol>`;
addRailTab("wire", "Wire", panel);
const log = panel.querySelector(".wire-log");
panel.querySelector(".wire-clear").addEventListener("click", clear);

const options = { hidePolls: true, follow: true };
for (const btn of panel.querySelectorAll(".wire-opt")) {
  btn.addEventListener("click", () => {
    const on = !options[btn.dataset.opt];
    options[btn.dataset.opt] = on;
    btn.setAttribute("aria-pressed", String(on));
    log.classList.toggle("hide-polls", options.hidePolls);
    if (options.follow) log.scrollTop = log.scrollHeight;
  });
}

// Follow only while the reader is at the bottom: an open row being read
// should not scroll away when the next frame lands.
let atBottom = true;
log.addEventListener("scroll", () => {
  atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
});

const toggle = document.createElement("button");
toggle.className = "ghost";
toggle.type = "button";
toggle.textContent = "Wire";
toggle.title = "Show the raw protocol frames";
toggle.setAttribute("aria-pressed", "false");
toggle.setAttribute("aria-controls", "rail");
toggle.addEventListener("click", () => toggleRail("wire"));
$("notif-btn").before(toggle);
document.addEventListener("rail:change", (e) => {
  const showing = e.detail.active === "wire";
  toggle.setAttribute("aria-pressed", String(showing));
  // Frames that arrived while the tab was hidden could not scroll it.
  if (showing && options.follow && atBottom) requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; });
});

const MAX_ROWS = 300;
const MAX_EXCHANGES_PER_ROW = 20;
const PENDING_MS = 60000;
const METHOD_LABEL = /^(GET|POST|PUT|PATCH|DELETE) (\S+)(.*)$/;

// Rows the server names by id (chat turns), and rows this page logged before
// calling the server, waiting for the exchange the call produces.
const rows = new Map();
let pending = [];
let lastRow = null;

// Capture runs while a page shows this tab: the server stops when the last
// events stream closes, so ask again on load and around each connection.
// Without app/wire_capture.py this 404s.
function startCapture() {
  postJson("/api/wire", { capture: true }).then(
    (body) => { hooks.wireCaptures = !!body?.capture; },
    () => { hooks.wireCaptures = false; },
  );
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
      if (row.background) row.count.textContent = `×${row.total}`;
      return;
    }
  }
  const row = addRow(frame);
  if (frame.id && frame.dir === "send") rows.set(frame.id, row);
  if (frame.exchange) addExchange(row, frame.exchange);
  else if (frame.dir === "send" && !frame.id) waitForExchange(row, frame.label);
};

// ---------- rows ----------

// "/rest/v1/conversations/conv_34kc.../messages" -> "/conversations/{id}/messages".
// The summary reads at a glance; the full URL is one click away.
function templatePath(path) {
  const bare = path.split("?")[0].replace(/^https?:\/\/[^/]+/, "").replace(/^\/rest\/v1(?=\/)/, "");
  return bare.split("/").map((seg) => (/^[a-z]+_[A-Za-z0-9]{8,}$/.test(seg) ? "{id}" : seg)).join("/");
}

function statusClass(exchange) {
  if (exchange.error || !exchange.status) return "err";
  if (exchange.status === 429) return "warn";
  return exchange.status >= 400 ? "err" : "ok";
}

function addRow(frame) {
  const li = document.createElement("li");
  const summary = document.createElement("div");
  summary.className = "wire-sum";
  const dir = document.createElement("span");
  dir.className = `dir ${frame.dir === "send" ? "send" : "recv"}`;
  dir.textContent = frame.dir === "send" ? "→" : "←";
  dir.title = frame.dir === "send" ? "Sent" : "Received";

  const label = document.createElement("span");
  label.className = "label";
  const call = METHOD_LABEL.exec(frame.label || "");
  if (call) {
    const method = document.createElement("span");
    method.className = `method m-${call[1].toLowerCase()}`;
    method.textContent = call[1];
    const path = document.createElement("span");
    path.className = "path";
    appendBreakable(path, templatePath(call[2]) + call[3]);
    label.append(method, path);
  } else {
    appendBreakable(label, frame.label || "");
  }

  const meta = document.createElement("span");
  meta.className = "meta";
  const count = document.createElement("span");
  count.className = "count";
  const status = document.createElement("span");
  status.className = "status";
  status.hidden = true;
  const ms = document.createElement("span");
  ms.className = "ms";
  meta.append(count, status, ms);

  const detail = document.createElement("span");
  detail.className = "detail";
  detail.textContent = frame.detail || "";
  // A captured call's detail is its status, which the pill already shows.
  detail.hidden = !frame.detail || (frame.exchange && frame.detail === String(frame.exchange.status || frame.exchange.error || ""));

  summary.append(dir, label, meta, detail);
  li.appendChild(summary);
  if (frame.background) li.classList.add("background");
  if (call && call[1] === "GET" && /\/events$/.test(call[2].split("?")[0])) li.dataset.poll = "1";
  if (frame.id) li.dataset.wireId = frame.id;
  log.appendChild(li);
  while (log.children.length > MAX_ROWS) {
    rows.delete(log.firstChild.dataset.wireId);
    log.removeChild(log.firstChild);
  }
  if (options.follow && atBottom && !(li.dataset.poll && options.hidePolls)) log.scrollTop = log.scrollHeight;
  lastRow = { li, summary, detail, status, ms, count, label: frame.label, background: !!frame.background, exchanges: [], total: 0, panel: null };
  return lastRow;
}

function addExchange(row, exchange) {
  exchange.receivedAt = new Date();
  row.exchanges.push(exchange);
  row.total += 1;
  exchange.seq = row.total;
  if (row.exchanges.length > MAX_EXCHANGES_PER_ROW) row.exchanges.shift();
  if (!row.panel) makeExpandable(row);
  if (!row.panel.hidden) appendExchange(row, exchange);
  row.status.hidden = false;
  row.status.className = `status ${statusClass(exchange)}`;
  row.status.textContent = exchange.error ? "error" : String(exchange.status);
  row.ms.textContent = exchange.elapsed_ms !== undefined ? `${exchange.elapsed_ms} ms` : "";
  if (row.detail.textContent === String(exchange.status || exchange.error || "")) row.detail.hidden = true;
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

// ---------- the expanded request and response ----------

// A folded row (repeated polls) can hold many calls: only the newest shows
// its bodies up front, the rest open on click like headers.
function renderExchanges(row) {
  const note = document.createElement("div");
  note.className = "wire-note";
  const newest = row.exchanges[row.exchanges.length - 1];
  const boxes = row.exchanges.map((ex) => renderExchange(ex, row.total > 1 ? ex.seq : 0, row.total > 1 && ex !== newest));
  row.panel.replaceChildren(note, ...boxes);
  updateNote(row);
}

// While a row is open, a new call is appended instead of re-rendering the
// row, so open blocks and scroll positions inside it stay put.
function appendExchange(row, exchange) {
  if (row.total === 2) return renderExchanges(row); // the first call now needs its number
  row.panel.appendChild(renderExchange(exchange, exchange.seq, false));
  const boxes = row.panel.querySelectorAll(".wire-ex");
  if (boxes.length > MAX_EXCHANGES_PER_ROW) boxes[0].remove();
  updateNote(row);
}

function updateNote(row) {
  const note = row.panel.querySelector(".wire-note");
  note.hidden = row.total <= row.exchanges.length;
  note.textContent = `Showing the last ${row.exchanges.length} of ${row.total} calls.`;
}

function renderExchange(ex, number, collapseBodies = false) {
  const box = document.createElement("div");
  box.className = "wire-ex";

  const request = section("Request", "req");
  const line = document.createElement("div");
  line.className = "wire-line";
  const method = document.createElement("span");
  method.className = `method m-${(ex.method || "").toLowerCase()}`;
  method.textContent = `${number ? `${number}. ` : ""}${ex.method}`;
  const url = document.createElement("span");
  url.className = "url";
  appendBreakable(url, ex.url || "");
  line.append(method, " ", url);
  request.body.appendChild(line);
  request.body.appendChild(headersBlock("Headers", ex.request_headers));
  if (ex.request_body) request.body.appendChild(block("Body", ex.request_body, { collapsed: collapseBodies }));

  const kind = statusClass(ex);
  const response = section("Response", `res ${kind}`);
  const outcome = document.createElement("div");
  outcome.className = "wire-line";
  const status = document.createElement("span");
  status.className = `status ${kind}`;
  status.textContent = ex.error ? ex.error : `${ex.status} ${ex.reason || ""}`.trim();
  const timing = document.createElement("span");
  timing.className = "timing";
  timing.textContent = [ex.elapsed_ms !== undefined ? `${ex.elapsed_ms} ms` : "", ex.receivedAt ? ex.receivedAt.toLocaleTimeString() : ""].filter(Boolean).join(" · ");
  outcome.append(status, timing);
  response.body.appendChild(outcome);
  if (ex.response_headers) response.body.appendChild(headersBlock("Headers", ex.response_headers));
  if (ex.response_body) response.body.appendChild(block(ex.streamed ? "Stream (raw events)" : "Body", ex.response_body, { collapsed: collapseBodies }));

  box.append(request.el, response.el);
  return box;
}

function section(title, kind) {
  const el = document.createElement("div");
  el.className = `wire-sec ${kind}`;
  const head = document.createElement("div");
  head.className = "wire-sec-title";
  head.textContent = title;
  const body = document.createElement("div");
  el.append(head, body);
  return { el, body };
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
  const body = document.createElement("div");
  body.className = "wire-text";
  if (collapsed) {
    name.type = "button";
    name.className = "wire-block-toggle disclosure";
    name.setAttribute("aria-expanded", "false");
    body.hidden = true;
    name.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!body.childElementCount) renderText(body, text);
      body.hidden = !body.hidden;
      name.setAttribute("aria-expanded", String(!body.hidden));
    });
  } else {
    renderText(body, text);
  }
  wrap.append(head, body);
  return wrap;
}

// Text wraps rather than scrolling sideways. Each line keeps its own
// indent, and a wrapped line continues two columns further in, so nesting
// still reads after a long value wraps.
function renderText(el, text) {
  const lines = document.createDocumentFragment();
  for (const raw of text.split("\n")) {
    const line = document.createElement("div");
    line.className = "wl";
    const indent = raw.length - raw.trimStart().length;
    if (indent) line.style.setProperty("--indent", String(indent));
    appendBreakable(line, raw.trimStart() || " ");
    lines.appendChild(line);
  }
  el.replaceChildren(lines);
}

// Long ids and URLs break after "/", "_", ".", "&", "=", "," before they
// break mid-word. <wbr> marks the spots without changing copied text.
function appendBreakable(el, text) {
  if (text.length < 32) {
    el.append(text);
    return;
  }
  const parts = text.split(/(?<=[/_.&=,])/);
  parts.forEach((part, i) => {
    el.append(part);
    if (i < parts.length - 1) el.appendChild(document.createElement("wbr"));
  });
}

function headerText(headers) {
  return Object.entries(headers || {}).map(([name, value]) => `${name}: ${value}`).join("\n");
}

// ---------- pairing calls with the exchanges they produce ----------

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
  atBottom = true;
}

document.addEventListener("app:disconnected", () => {
  clear();
  startCapture();
});
