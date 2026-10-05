/* Optional: the reasoning trail.

   While the assistant works, the stream carries REASONING_MESSAGE outputs: a
   running, cumulative list of steps. This module renders them as a live
   trail above the answer and collapses it once the answer starts. It pairs
   with server-side streaming (app/streaming.py); without streaming there are
   no reasoning outputs to show. Delete this file and thinking.css, plus the
   import in main.js, to drop the trail. */
import { hooks, useStylesheet } from "../hooks.js";
import { escapeHtml, mdInline } from "../markdown.js";
import { scrollDown } from "../scroll.js";

useStylesheet(new URL("./thinking.css", import.meta.url));

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

hooks.thinking = (turn) => {
  const el = document.createElement("div");
  el.className = "thinking open active";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "thinking-summary";
  btn.textContent = "THINKING";
  const wrap = document.createElement("div");
  wrap.className = "trail-wrap";
  wrap.style.maxHeight = "none";
  const trail = document.createElement("div");
  trail.className = "trail";
  wrap.appendChild(trail);
  el.append(btn, wrap);
  turn.appendChild(el);

  const expand = () => {
    el.classList.add("open");
    wrap.style.maxHeight = wrap.scrollHeight + "px";
    setTimeout(() => { if (el.classList.contains("open")) wrap.style.maxHeight = "none"; }, 380);
  };
  // Collapsing instantly snaps the layout, so animate max-height instead.
  const collapse = () => {
    if (reducedMotion()) {
      el.classList.remove("open");
      wrap.style.maxHeight = "0px";
      return;
    }
    wrap.style.maxHeight = wrap.scrollHeight + "px";
    void wrap.offsetHeight; // reflow so the transition has a start value
    el.classList.remove("open");
    wrap.style.maxHeight = "0px";
    scrollDown(420);
  };
  btn.addEventListener("click", () => (el.classList.contains("open") ? collapse() : expand()));

  // Mark the in-flight step complete rather than leaving an arrow behind.
  const markStepsDone = () => {
    for (const step of trail.querySelectorAll(".step.now")) {
      step.classList.remove("now");
      step.classList.add("done");
      const mark = step.querySelector(".mark");
      if (mark) mark.textContent = "✓";
    }
  };
  let settled = false;
  // Called when the answer starts, so the collapse and the answer's
  // appearance share one transition instead of two separate shifts.
  const settle = () => {
    markStepsDone();
    if (settled) return;
    settled = true;
    if (!trail.textContent.trim()) { el.remove(); return; }
    collapse();
  };

  return {
    update: (text) => renderTrail(trail, text),
    settle,
    finish() {
      el.classList.remove("active");
      settle();
    },
  };
};

// Snapshots are cumulative (each contains all prior lines plus new ones).
// Reconcile against what's shown so only genuinely new steps animate in.
function renderTrail(trail, raw) {
  const lines = raw.split("\n").map((l) => l.trim()).filter(Boolean);
  const existing = trail.children;
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i];
    const prior = existing[i];
    if (prior && prior.dataset.raw === t) continue;
    const step = document.createElement("div");
    step.className = "step";
    step.dataset.raw = t;
    const mark = document.createElement("span");
    mark.className = "mark";
    let rest = t;
    if (t.startsWith("✓")) { step.classList.add("done"); mark.textContent = "✓"; rest = t.slice(1); }
    else if (t.startsWith("→")) { step.classList.add("now"); mark.textContent = "→"; rest = t.slice(1); }
    else if (/^[⏳⌛]/u.test(t)) { step.classList.add("head"); rest = t.replace(/^[⏳⌛]\s*/u, ""); }
    const body = document.createElement("span");
    body.innerHTML = mdInline(escapeHtml(rest.trim()));
    step.append(mark, body);
    if (prior) trail.replaceChild(step, prior);
    else trail.appendChild(step);
  }
  while (existing.length > lines.length) trail.removeChild(trail.lastChild);
}
