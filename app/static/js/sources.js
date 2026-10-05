import { hooks } from "./hooks.js";

// Collapsed by default: the answer is the point, sources are for checking it.
export function renderSources(turn, sources, live) {
  let box = turn.querySelector(".sources");
  if (!sources || !sources.length) {
    if (box) box.remove();
    return;
  }
  if (!box) {
    box = document.createElement("div");
    box.className = "sources";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "sources-toggle";
    const list = document.createElement("div");
    list.className = "sources-list";
    list.hidden = true;
    toggle.addEventListener("click", () => {
      hooks.animateHeight(box, () => {
        list.hidden = !list.hidden;
        box.classList.toggle("open", !list.hidden);
      }, { minMs: 300 });
    });
    box.append(toggle, list);
    turn.appendChild(box);
    if (live) hooks.reveal(box);
  }
  box.querySelector(".sources-toggle").textContent = `Sources (${sources.length})`;
  box.querySelector(".sources-list").replaceChildren(...sources.map(sourceItem));
}

function sourceItem(source) {
  const item = document.createElement(source.url ? "a" : "div");
  item.className = "source";
  if (source.url) {
    item.href = source.url;
    item.target = "_blank";
    item.rel = "noopener";
  }
  const title = document.createElement("div");
  title.className = "source-title";
  title.textContent = source.title;
  item.appendChild(title);
  const meta = [];
  if (source.url) {
    try { meta.push(new URL(source.url).hostname); } catch { /* not a URL; skip the host */ }
  }
  if (source.state) {
    const state = source.state.replace(/_/g, " ").toLowerCase(); // "IN_PROGRESS" -> "In progress"
    meta.push(state.charAt(0).toUpperCase() + state.slice(1));
  }
  if (source.updated) {
    const when = new Date(source.updated);
    if (!Number.isNaN(when.getTime())) meta.push(`Updated ${when.toLocaleDateString(undefined, { month: "short", year: "numeric" })}`);
  }
  if (meta.length) {
    const m = document.createElement("div");
    m.className = "source-meta";
    m.textContent = meta.join(" · ");
    item.appendChild(m);
  }
  if (source.snippet) {
    const snippet = document.createElement("div");
    snippet.className = "source-snippet";
    snippet.textContent = source.snippet;
    item.appendChild(snippet);
  }
  return item;
}
