import { postJson } from "./api.js";
import { $, emit } from "./dom.js";
import { hooks } from "./hooks.js";
import { renderMarkdown } from "./markdown.js";
import { scrollDown } from "./scroll.js";
import { chatLog } from "./chat.js";
import { lockActionRow } from "./actions.js";

let current = emptyModalState();

function emptyModalState() {
  // `options`: choice options from the first load, which refreshes omit.
  return { ref: null, fields: [], chip: null, options: new Map(), refreshSeq: 0 };
}

function fetchModal(ref) {
  return postJson("/api/modal", ref, "Could not load the form.");
}

// Modal ids are minted per message read, so a cache only lives as long as
// the page's copy of that message. A failed read is dropped so a later
// attempt can try again.
const modalCache = new Map();
export function fetchModalCached(ref) {
  if (!modalCache.has(ref.modal_id)) {
    const read = throttled(() => fetchModal(ref));
    read.catch(() => modalCache.delete(ref.modal_id));
    modalCache.set(ref.modal_id, read);
  }
  return modalCache.get(ref.modal_id);
}

// Form cards read their modal ahead of a click. A long history can offer
// dozens of forms, and reading them all at once trips the API's rate limit,
// so these reads go two at a time and a failure gets one retry.
const PREFETCH_CONCURRENCY = 2;
const PREFETCH_RETRY_MS = 3000;
let running = 0;
let waiting = [];
let generation = 0;

function throttled(task) {
  return new Promise((resolve, reject) => {
    waiting.push({ run: () => task().then(resolve, reject), reject });
    pump();
  });
}

function pump() {
  while (running < PREFETCH_CONCURRENCY && waiting.length) {
    running += 1;
    waiting.shift().run().finally(() => { running -= 1; pump(); });
  }
}

export function prefetchModal(ref) {
  const started = generation;
  return fetchModalCached(ref).catch((e) => {
    if (e.name === "AbortError" || started !== generation) throw e;
    return new Promise((resolve) => setTimeout(resolve, PREFETCH_RETRY_MS)).then(() => {
      if (started !== generation) throw new DOMException("Conversation changed.", "AbortError");
      return fetchModalCached(ref);
    });
  });
}

// Leaving a conversation drops its queued reads, so the next conversation's
// cards do not wait behind them, and its cached modals (ids are minted per read).
export function cancelPrefetch() {
  generation += 1;
  const dropped = waiting;
  waiting = [];
  for (const item of dropped) item.reject(new DOMException("Conversation changed.", "AbortError"));
  modalCache.clear();
}

export async function openModal(ref, label, sourceChip) {
  current = { ...emptyModalState(), ref, chip: sourceChip || null, label: label || "Form" };
  $("modal-title").textContent = current.label;
  $("modal-kicker").hidden = true;
  $("modal-error").hidden = true;
  const form = $("modal-form");
  form.innerHTML = `<p class="modal-loading">Loading the form…</p>`;
  $("modal-backdrop").hidden = false;
  $("modal-close").focus();
  hooks.wire({ dir: "send", label: "GET .../modals/{id}" });
  try {
    const modal = await fetchModal(ref);
    hooks.wire({ dir: "recv", label: `modal "${modal.title || ""}"`, detail: `${(modal.fields || []).length} fields` });
    for (const f of modal.fields || []) {
      if (f.type === "CHOICE" && ((f.choice || {}).options || []).length) current.options.set(f.name, f.choice.options);
    }
    buildModalForm(modal);
  } catch (e) {
    form.innerHTML = "";
    showError(String(e.message || e));
  }
}

function buildModalForm(modal) {
  const form = $("modal-form");
  form.innerHTML = "";
  current.fields = [];
  const header = setModalHeading(modal);
  for (const f of modal.fields || []) {
    if (f.visible === false || f === header) continue;
    if (f.type === "DISPLAY") {
      form.appendChild(displayField(f.display || {}));
      continue;
    }

    const wrap = document.createElement("label");
    wrap.className = "field";
    const span = document.createElement("span");
    span.textContent = f.label || f.name;
    if (f.required) span.innerHTML += ' <span class="req">*</span>';
    wrap.appendChild(span);

    // A FILE value is an upload id from the Files API; the attachments module uploads.
    const control = f.type === "FILE" ? hooks.fileField?.(f, current.ref) : inputFor(f, wrap);
    if (!control && f.type === "FILE") {
      const note = document.createElement("small");
      note.textContent = "File fields are not supported in this app.";
      wrap.appendChild(note);
      form.appendChild(wrap);
      continue;
    }
    if (!control) continue;
    const { input, getValue, pending } = control;
    if (f.read_only && input.tagName !== "DIV") input.disabled = true;
    if (f.triggers_refresh) input.addEventListener("change", refreshModal);
    wrap.appendChild(input);
    if (f.description) {
      const small = document.createElement("small");
      small.textContent = f.description;
      wrap.appendChild(small);
    }
    form.appendChild(wrap);
    current.fields.push({ field: f, getValue, pending });
  }
}

// Forms often open with a DISPLAY header ("New request") under a generic
// title ("File ticket"). Show one heading: the header, with the title above
// it as a small label. Returns the header field it used, if any.
function setModalHeading(modal) {
  const title = (modal.title || current.label || "Form").trim();
  const first = (modal.fields || []).find((f) => f.visible !== false);
  const text = first?.type === "DISPLAY" && first.display?.text;
  const heading = text && (text.style || "").toUpperCase() === "HEADER" ? text.text.replace(/\*\*/g, "").trim() : "";
  const kicker = $("modal-kicker");
  $("modal-title").textContent = heading || title;
  kicker.textContent = title;
  kicker.hidden = !heading || heading.toLowerCase() === title.toLowerCase();
  return heading ? first : null;
}

function displayField(d) {
  const el = document.createElement("div");
  if (d.text) {
    const style = (d.text.style || "NORMAL").toLowerCase();
    el.className = `display-${style === "header" ? "header" : style === "subtle" ? "subtle" : "normal"}`;
    if (style === "normal") el.classList.add("modal-desc");
    renderMarkdown(el, d.text.text || "");
  } else if (d.image?.url) {
    const img = document.createElement("img");
    img.src = d.image.url;
    img.alt = "";
    img.style.maxWidth = "100%";
    el.appendChild(img);
  } else if (d.download && !d.download.url) {
    el.textContent = d.download.text || "Download";
  } else if (d.download) {
    const a = document.createElement("a");
    a.href = d.download.url;
    a.textContent = d.download.text || "Download";
    a.target = "_blank";
    a.rel = "noopener";
    el.appendChild(a);
  }
  return el;
}

// One input per field type; getValue returns the API's value shape, or null when empty.
function inputFor(f, wrap) {
  if (f.type === "TEXT") {
    const cfg = f.text || {};
    const input = document.createElement(cfg.multiline ? "textarea" : "input");
    if (!cfg.multiline) input.type = "text";
    input.value = cfg.prefill || "";
    return { input, getValue: () => (input.value ? { text: input.value } : null) };
  }
  if (f.type === "DATE") {
    const input = document.createElement("input");
    input.type = "date";
    input.value = (f.date || {}).prefill || "";
    return { input, getValue: () => (input.value ? { date: input.value } : null) };
  }
  if (f.type === "DATETIME") {
    const input = document.createElement("input");
    input.type = "datetime-local";
    const pre = (f.datetime || {}).prefill || "";
    if (pre) input.value = pre.slice(0, 16);
    return { input, getValue: () => (input.value ? { datetime: new Date(input.value).toISOString() } : null) };
  }
  if (f.type === "BOOLEAN") {
    wrap.classList.add("toggle-row");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean((f.boolean || {}).prefill);
    return { input, getValue: () => ({ bool: input.checked }) };
  }
  if (f.type === "CHOICE") return choiceInput(f, wrap);
  return null;
}

function choiceInput(f, wrap) {
  const cfg = f.choice || {};
  // A CHOICE can ship zero options but a prefill (server-resolved values,
  // e.g. the requesting user). Treat prefill as the option set so the field
  // is not silently blank.
  const opts = (cfg.options && cfg.options.length) ? cfg.options : (cfg.prefill || []);
  if (cfg.multi && !opts.length) {
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Type a value";
    return { input, getValue: () => (input.value ? { options: [input.value] } : null) };
  }
  if (cfg.multi) {
    const input = document.createElement("div");
    const boxes = opts.map((o) => {
      const row = document.createElement("label");
      row.className = "toggle-row";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = o.value;
      cb.checked = (cfg.prefill || []).some((p) => p.value === o.value);
      row.append(cb, document.createTextNode(" " + (o.display_value || o.value)));
      input.appendChild(row);
      return cb;
    });
    return {
      input,
      getValue: () => {
        const chosen = boxes.filter((b) => b.checked).map((b) => b.value);
        return chosen.length ? { options: chosen } : null;
      },
    };
  }
  if (cfg.allows_free_input) {
    const input = document.createElement("input");
    input.type = "text";
    const listId = `dl-${f.name.replace(/\W/g, "_")}`;
    const dl = document.createElement("datalist");
    dl.id = listId;
    for (const o of opts) {
      const op = document.createElement("option");
      op.value = o.value;
      op.label = o.display_value || o.value;
      dl.appendChild(op);
    }
    input.setAttribute("list", listId);
    input.value = ((cfg.prefill || [])[0] || {}).value || "";
    wrap.appendChild(dl);
    return { input, getValue: () => (input.value ? { option: input.value } : null) };
  }
  const input = document.createElement("select");
  if (!f.required) input.appendChild(new Option("", ""));
  for (const o of opts) input.appendChild(new Option(o.display_value || o.value, o.value));
  const pre = ((cfg.prefill || [])[0] || {}).value;
  if (pre) input.value = pre;
  return { input, getValue: () => (input.value ? { option: input.value } : null) };
}

function enteredValues() {
  const values = {};
  for (const { field, getValue } of current.fields) {
    const v = getValue();
    if (v !== null) values[field.name] = v;
  }
  return values;
}

// A field flagged triggers_refresh can change other fields' state; the
// server re-resolves the whole form from every value entered so far.
async function refreshModal() {
  const seq = ++current.refreshSeq;
  const ref = current.ref;
  const values = enteredValues();
  hooks.wire({ dir: "send", label: "POST .../modals/{id}/refresh", detail: `${Object.keys(values).length} values` });
  try {
    const modal = await postJson("/api/modal/refresh", { ...ref, values }, "Could not update the form.");
    if (seq !== current.refreshSeq || ref !== current.ref) return;
    hooks.wire({ dir: "recv", label: "modal refreshed", detail: `${(modal.fields || []).length} fields` });
    $("modal-error").hidden = true;
    buildModalForm({ ...modal, fields: (modal.fields || []).map((f) => restoreField(f, values[f.name])) });
  } catch (e) {
    if (seq === current.refreshSeq) showError(String(e.message || e));
  }
}

// Re-apply what a refresh leaves out: choice options, and the user's entry
// wherever the server did not resolve a value of its own.
function restoreField(f, v) {
  const field = { ...f };
  if (field.type === "CHOICE") {
    const choice = { ...(field.choice || {}) };
    if (!(choice.options || []).length) choice.options = current.options.get(field.name) || [];
    if (v && !(choice.prefill || []).length) {
      choice.prefill = v.options ? v.options.map((value) => ({ value })) : v.option ? [{ value: v.option }] : [];
    }
    field.choice = choice;
    return field;
  }
  if (!v) return field;
  const slot = { TEXT: ["text", v.text], DATE: ["date", v.date], DATETIME: ["datetime", v.datetime], BOOLEAN: ["boolean", v.bool] }[field.type];
  if (slot && slot[1] !== undefined) {
    const cfg = field[slot[0]] || {};
    if (cfg.prefill === undefined || cfg.prefill === null || cfg.prefill === "") field[slot[0]] = { ...cfg, prefill: slot[1] };
  }
  return field;
}

export async function submitModal() {
  $("modal-error").hidden = true;
  const values = {};
  for (const { field, getValue, pending } of current.fields) {
    if (pending?.()) return showError(`Wait for the upload in "${field.label || field.name}" to finish.`);
    const v = getValue();
    if (v === null) {
      if (field.required && !field.read_only) return showError(`"${field.label || field.name}" is required.`);
      continue;
    }
    values[field.name] = v;
  }
  const btn = $("modal-submit");
  btn.disabled = true;
  btn.textContent = "Submitting…";
  hooks.wire({ dir: "send", label: "POST .../modals/{id}/submit", detail: `${Object.keys(values).length} values` });
  try {
    const body = await postJson("/api/modal/submit", { ...current.ref, values }, "Submission failed.");
    hooks.wire({ dir: "recv", label: `submit ${body.status || "SUBMITTED"}` });
    if (current.chip) lockActionRow(current.chip);
    closeModal();
    // Only echo a receipt the server actually sent. The submit ack is not
    // completion: the assistant's own follow-ups narrate the real outcome.
    if (body.receipt_text) {
      const line = document.createElement("div");
      line.className = "sysline";
      line.textContent = `✓ ${body.receipt_text}`;
      chatLog.appendChild(line);
      scrollDown();
    }
  } catch (e) {
    showError(String(e.message || e));
  } finally {
    btn.disabled = false;
    btn.textContent = "Submit";
  }
}

export function closeModal() {
  const opener = current.chip;
  $("modal-backdrop").hidden = true;
  current = emptyModalState();
  if (opener?.isConnected && !opener.disabled) opener.focus();
  emit("app:modal-closed");
}

function showError(text) {
  const err = $("modal-error");
  err.textContent = text;
  err.hidden = false;
}
