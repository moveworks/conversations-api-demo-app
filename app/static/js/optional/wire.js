/* Optional: the wire pane, a live log of every protocol frame.

   The server tags each Conversations API call and stream event with a
   "wire" frame; this module shows them in a side pane toggled from the top
   bar. It is a teaching and debugging aid, not part of a chat surface.
   Delete this file and wire.css, plus the import in main.js, to drop it. */
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
pane.querySelector(".wire-clear").addEventListener("click", () => log.replaceChildren());

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

hooks.wire = (frame) => {
  const li = document.createElement("li");
  const dir = document.createElement("span");
  dir.className = `dir ${frame.dir === "send" ? "send" : ""}`;
  dir.textContent = frame.dir === "send" ? "→" : "←";
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = frame.label || "";
  li.append(dir, label);
  if (frame.detail) {
    const detail = document.createElement("span");
    detail.className = "detail";
    detail.textContent = frame.detail;
    li.appendChild(detail);
  }
  log.appendChild(li);
  while (log.children.length > 300) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
};

document.addEventListener("app:disconnected", () => log.replaceChildren());
