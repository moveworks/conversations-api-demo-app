/* Optional: animate layout changes instead of snapping them.

   Answer snapshots arrive in large chunks, so a bubble can grow hundreds of
   pixels at once. This module tweens those height changes, keeps the chat
   glued to the bottom while they run, fades new blocks in, and staggers
   form cards that arrive together. Delete this file and motion.css, plus the
   import in main.js, and every change simply applies instantly. */
import { $ } from "../dom.js";
import { hooks, useStylesheet } from "../hooks.js";
import { pinToBottom } from "../scroll.js";

useStylesheet(new URL("./motion.css", import.meta.url));

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let followUntil = 0;
let following = false;
hooks.follow = (ms = 0) => {
  followUntil = Math.max(followUntil, performance.now() + ms);
  if (following) return;
  following = true;
  const tick = () => {
    pinToBottom();
    if (performance.now() < followUntil) requestAnimationFrame(tick);
    else following = false;
  };
  tick();
};

// Safe to call again mid-tween: it restarts from the current height.
hooks.animateHeight = (el, mutate, { from = null, minMs = 0 } = {}) => {
  const start = from ?? el.getBoundingClientRect().height;
  el.style.transition = "";
  el.style.height = "";
  mutate();
  const end = el.getBoundingClientRect().height;
  if (reducedMotion() || Math.abs(end - start) < 2) {
    el.style.overflow = "";
    hooks.follow();
    return;
  }
  const ms = Math.max(minMs, Math.round(Math.min(900, 200 + Math.abs(end - start) * 0.7)));
  el.style.overflow = "hidden";
  el.style.height = start + "px";
  void el.offsetHeight; // reflow so the transition has a start value
  el.style.transition = `height ${ms}ms cubic-bezier(0.45, 0, 0.25, 1)`;
  el.style.height = end + "px";
  const token = (el._tween = (el._tween || 0) + 1);
  setTimeout(() => {
    if (el._tween !== token) return;
    el.style.transition = el.style.height = el.style.overflow = "";
  }, ms + 30);
  hooks.follow(ms + 60);
};

hooks.fadeIn = (el) => el.classList.add("reveal");

hooks.reveal = (el) => {
  el.classList.add("reveal");
  hooks.animateHeight(el, () => {}, { from: 0 });
};

// Modal fetches resolve together; enter the cards one after another rather
// than popping them all in at once. Action order is kept by the caller.
hooks.enterCard = (insert, card, target) => {
  const now = performance.now();
  const at = Math.max(now, target._nextCardAt || 0);
  target._nextCardAt = at + 160;
  setTimeout(() => {
    card.classList.add("card-in");
    insert();
  }, at - now);
};

document.addEventListener("app:connected", () => {
  const dot = $("status-dot");
  dot.classList.remove("pulse");
  void dot.offsetWidth; // restart the animation
  dot.classList.add("pulse");
});
