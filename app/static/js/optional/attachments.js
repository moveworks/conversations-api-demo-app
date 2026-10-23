/* Optional: attach files to messages and to modal FILE fields.

   Adds an attach button to the composer, plus drag and drop and paste. Each
   file uploads as soon as it is picked (app/attachments.py forwards it to
   the Files API) and shows as a chip with its size and progress; Send waits
   until every upload has finished. The turn carries the uploads' ids as
   chat.files. Modal FILE fields get the same uploader, and submit the ids as
   the field's value. The Files API is in alpha and enabled per Moveworks
   instance, and uploads expire after 24 hours. Nothing is added unless the
   server answers GET /api/files, so without app/attachments.py (or with a
   starter that has no Files API helper) the app behaves as if this module
   were absent.
   Delete this file and attachments.css, plus the import in main.js and
   app/attachments.py, to drop it. */
import { apiDetail, getJson } from "../api.js";
import { $ } from "../dom.js";
import { hooks, useStylesheet } from "../hooks.js";
import { toast } from "../toast.js";

const MAX_BYTES = 100 * 1024 * 1024;
const MAX_FILES_PER_TURN = 5;
const PAPERCLIP = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5l-8.6 8.6a5.5 5.5 0 01-7.8-7.8l8.6-8.6a3.7 3.7 0 015.2 5.2l-8.6 8.6a1.8 1.8 0 01-2.6-2.6l7.9-7.9"/></svg>`;

// ---------- one upload, shown as a chip ----------

// XMLHttpRequest rather than fetch: only it reports upload progress.
function send(file, onProgress) {
  const xhr = new XMLHttpRequest();
  const done = new Promise((resolve, reject) => {
    xhr.open("POST", "/api/files");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.upload.addEventListener("progress", (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); });
    xhr.addEventListener("load", () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON error page */ }
      if (xhr.status >= 200 && xhr.status < 300 && body?.id) resolve(body);
      else reject(new Error(apiDetail(body, `Upload failed (${xhr.status}).`)));
    });
    xhr.addEventListener("error", () => reject(new Error("Upload failed: the app's server did not answer.")));
    xhr.addEventListener("abort", () => reject(new DOMException("Upload cancelled.", "AbortError")));
    xhr.send(file);
  });
  return { done, abort: () => xhr.abort() };
}

// Uploads that count toward a limit: failed ones are shown but never sent.
const isActive = (item) => item.state === "uploading" || item.state === "ready";

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// An upload's state: "uploading", "ready" (has an id), or "failed" (has an error).
// `onChange` is reassigned when a modal re-renders the field that holds it.
function startUpload(file, onChange) {
  const item = { name: file.name, size: file.size, state: "uploading", progress: 0, id: null, error: "", abort: () => {}, onChange };
  item.chip = chipFor(item);
  const fail = (message) => {
    item.state = "failed";
    item.error = message;
    paint(item);
    item.onChange(item);
  };
  if (!file.size) {
    fail("The file is empty.");
    return item;
  }
  if (file.size > MAX_BYTES) {
    fail("Larger than the 100 MB limit.");
    return item;
  }
  // With the server's wire capture on, the real exchange is its own row.
  const logRows = !hooks.wireCaptures;
  if (logRows) hooks.wire({ dir: "send", label: "POST /files", detail: `${file.name} (${formatSize(file.size)})` });
  const upload = send(file, (fraction) => {
    item.progress = fraction;
    paint(item);
  });
  item.abort = upload.abort;
  upload.done.then(
    (uploaded) => {
      item.state = "ready";
      item.id = uploaded.id;
      if (logRows) hooks.wire({ dir: "recv", label: `file ${uploaded.content_type || ""}`.trim(), detail: uploaded.id });
      paint(item);
      item.onChange(item);
    },
    (e) => {
      if (e.name === "AbortError") return;
      if (logRows) hooks.wire({ dir: "recv", label: "upload failed", detail: e.message });
      fail(e.message);
    },
  );
  return item;
}

function chipFor(item) {
  const chip = document.createElement("span");
  chip.className = "file-chip";
  const name = document.createElement("span");
  name.className = "file-chip-name";
  name.textContent = item.name;
  name.title = item.name;
  const meta = document.createElement("span");
  meta.className = "file-chip-meta";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "file-chip-remove";
  remove.textContent = "✕";
  remove.setAttribute("aria-label", `Remove ${item.name}`);
  remove.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    item.abort();
    item.state = "removed";
    chip.remove();
    item.onChange(item);
  });
  chip.append(name, meta, remove);
  item.meta = meta;
  paint(item, chip);
  return chip;
}

function paint(item, chip = item.chip) {
  chip.dataset.state = item.state;
  chip.style.setProperty("--progress", `${Math.round(item.progress * 100)}%`);
  const size = formatSize(item.size);
  item.meta.textContent = item.state === "uploading" ? `${size} · ${Math.round(item.progress * 100)}%`
    : item.state === "ready" ? `${size} · ready`
    : item.error;
  chip.title = item.state === "failed" ? `${item.name}: ${item.error}` : item.name;
}

// Not awaited at the top level, so the app boots without waiting on it.
getJson("/api/files").then(
  (capability) => { if (capability?.enabled) install(); },
  () => { /* no server half: leave the composer and FILE fields as they are */ },
);

function install() {
  useStylesheet(new URL("./attachments.css", import.meta.url));

  // ---------- composer ----------

  const composer = $("composer");
  const tray = document.createElement("div");
  tray.className = "attach-tray";
  tray.hidden = true;
  tray.setAttribute("aria-label", "Attached files");
  composer.before(tray);

  const picker = document.createElement("input");
  picker.type = "file";
  picker.multiple = true;
  picker.hidden = true;
  composer.appendChild(picker);

  const attachBtn = document.createElement("button");
  attachBtn.type = "button";
  attachBtn.className = "ghost icon-btn attach-btn";
  attachBtn.title = "Attach files";
  attachBtn.setAttribute("aria-label", "Attach files");
  attachBtn.innerHTML = PAPERCLIP;
  attachBtn.addEventListener("click", () => picker.click());
  $("chat-input").before(attachBtn);

  let composerItems = [];

  function composerChanged() {
    composerItems = composerItems.filter((item) => item.state !== "removed");
    tray.hidden = !composerItems.length;
    $("send-btn").disabled = composerItems.some((item) => item.state === "uploading");
  }

  function attachToComposer(files) {
    for (const file of files) {
      if (composerItems.filter(isActive).length >= MAX_FILES_PER_TURN) {
        toast("A message can carry up to five files.");
        break;
      }
      const item = startUpload(file, composerChanged);
      composerItems.push(item);
      tray.appendChild(item.chip);
    }
    composerChanged();
  }

  picker.addEventListener("change", () => {
    attachToComposer([...picker.files]);
    picker.value = "";
  });

  composer.addEventListener("dragover", (e) => {
    if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
    e.preventDefault();
    composer.classList.add("drag-over");
  });
  composer.addEventListener("dragleave", (e) => {
    if (!composer.contains(e.relatedTarget)) composer.classList.remove("drag-over");
  });
  composer.addEventListener("drop", (e) => {
    composer.classList.remove("drag-over");
    if (!e.dataTransfer?.files.length) return;
    e.preventDefault();
    attachToComposer([...e.dataTransfer.files]);
  });
  // A drop that misses the composer would otherwise open the file in place of the app.
  for (const type of ["dragover", "drop"]) {
    document.addEventListener(type, (e) => {
      if ([...(e.dataTransfer?.types || [])].includes("Files")) e.preventDefault();
    });
  }
  $("chat-input").addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;
    // Copying from some apps puts both the text and an image of it on the clipboard; keep the text.
    if (!e.clipboardData.types.includes("text/plain")) e.preventDefault();
    attachToComposer(files);
  });

  hooks.composerFiles = () => ({
    files: composerItems.filter((item) => item.state === "ready").map(({ id, name }) => ({ id, name })),
    busy: composerItems.some((item) => item.state === "uploading"),
  });

  // After a send only the files it carried go; failed chips stay so the user sees why.
  hooks.clearComposerFiles = () => {
    for (const item of composerItems) if (item.state === "ready") item.chip.remove();
    composerItems = composerItems.filter((item) => item.state !== "ready");
    composerChanged();
  };

  document.addEventListener("app:disconnected", () => {
    for (const item of composerItems) {
      item.abort();
      item.chip.remove();
    }
    composerItems = [];
    composerChanged();
  });

  // ---------- modal FILE fields ----------

  // Uploads survive the re-render a refresh causes, keyed by modal and field.
  let modalUploads = { modalId: null, byField: new Map() };
  document.addEventListener("app:modal-closed", () => {
    for (const items of modalUploads.byField.values()) for (const item of items) item.abort();
    modalUploads = { modalId: null, byField: new Map() };
  });

  hooks.fileField = (field, ref) => {
    if (modalUploads.modalId !== ref?.modal_id) modalUploads = { modalId: ref?.modal_id, byField: new Map() };
    if (!modalUploads.byField.has(field.name)) modalUploads.byField.set(field.name, []);
    const items = modalUploads.byField.get(field.name);
    const maxFiles = Math.max(1, (field.file || {}).max_files || 1);

    const input = document.createElement("div");
    input.className = "file-field";
    const choose = document.createElement("button");
    choose.type = "button";
    choose.className = "ghost small";
    const list = document.createElement("div");
    list.className = "file-field-list";
    const filePicker = document.createElement("input");
    filePicker.type = "file";
    filePicker.multiple = maxFiles > 1;
    filePicker.hidden = true;
    input.append(choose, filePicker, list);

    const live = () => items.filter((item) => item.state !== "removed");
    const update = (item) => {
      const kept = live();
      items.splice(0, items.length, ...kept);
      choose.textContent = maxFiles > 1 ? `Choose files (up to ${maxFiles})` : kept.length ? "Replace file" : "Choose file";
      choose.disabled = !!field.read_only || (maxFiles > 1 && kept.filter(isActive).length >= maxFiles);
      // Report a value change only once an upload has an id (or is removed), so
      // a triggers_refresh field refreshes with the id in place.
      if (item && (item.state === "ready" || item.state === "removed")) input.dispatchEvent(new Event("change"));
    };
    for (const item of items) {
      item.onChange = update;
      list.appendChild(item.chip);
    }
    update(null);

    choose.addEventListener("click", (e) => {
      e.preventDefault();
      filePicker.click();
    });
    filePicker.addEventListener("change", (e) => {
      // The field changes when the upload finishes, not when the file is picked.
      e.stopPropagation();
      const picked = [...filePicker.files];
      filePicker.value = "";
      if (maxFiles === 1) for (const old of live()) { old.abort(); old.state = "removed"; old.chip.remove(); }
      const room = maxFiles - live().filter(isActive).length;
      for (const file of picked.slice(0, room)) {
        const item = startUpload(file, update);
        items.push(item);
        list.appendChild(item.chip);
      }
      if (picked.length > room) toast(`This field takes at most ${maxFiles} file${maxFiles === 1 ? "" : "s"}.`);
      update(null);
    });

    return {
      input,
      getValue: () => {
        const ids = live().filter((item) => item.state === "ready").map((item) => ({ file_id: item.id }));
        return ids.length ? { files: ids } : null;
      },
      pending: () => live().some((item) => item.state === "uploading"),
    };
  };
}
