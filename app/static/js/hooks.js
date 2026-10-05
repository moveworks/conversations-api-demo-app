/* Extension points for the optional modules in ./optional/.

   Core code always calls through these. Each default is the plain behavior,
   so deleting an optional module (and its import in main.js) leaves a
   working app. */

export const hooks = {
  // motion.js: animate layout changes instead of snapping.
  animateHeight: (el, mutate) => mutate(),
  fadeIn: () => {},
  reveal: () => {},
  enterCard: (insert) => insert(),
  follow: null, // (ms) => keep the chat pinned to the bottom for ms

  // wire.js: log a protocol frame.
  wire: () => {},

  // thinking.js: (turn) => { update(text), settle(), finish() } for the reasoning trail.
  thinking: null,

  // test-notify.js: a proactive notification arrived, in the thread or the drawer.
  notificationArrived: () => {}, // (data, element) => void

  // inline-forms.js: render an answer split around a slot for form cards.
  renderAnswer: null, // (bubble, layout) => void
  formSlot: () => null, // (turn) => element that holds form cards, or null
};

export function useStylesheet(url) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = url;
  document.head.appendChild(link);
}
