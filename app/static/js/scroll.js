import { $ } from "./dom.js";
import { hooks } from "./hooks.js";

const chatScroll = $("chat-scroll");

// Follow the bottom only while the reader is there; scrolling up to reread
// should not get yanked back down by the next update.
let stuck = true;
chatScroll.addEventListener("scroll", () => {
  stuck = chatScroll.scrollHeight - chatScroll.scrollTop - chatScroll.clientHeight < 80;
});

export function pinToBottom() {
  if (stuck) chatScroll.scrollTop = chatScroll.scrollHeight;
}

export function stickToBottom() {
  stuck = true;
}

// `ms` is how long content is still moving (an animation); motion.js keeps
// re-pinning for that long. Without it, pin once.
export function scrollDown(ms = 0) {
  if (hooks.follow) hooks.follow(ms);
  else pinToBottom();
}
