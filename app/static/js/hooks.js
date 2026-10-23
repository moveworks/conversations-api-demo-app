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
  wireCaptures: false, // true once the server records full exchanges for the pane

  // thinking.js: (turn) => { update(text), settle(), finish() } for the reasoning trail.
  thinking: null,

  // test-notify.js: a proactive notification arrived, in the thread or on the Notifications tab.
  notificationArrived: () => {}, // (data, element) => void

  // attachments.js: files attached in the composer, and modal FILE fields.
  composerFiles: () => ({ files: [], busy: false }), // files: [{ id, name }]; busy while an upload runs
  clearComposerFiles: () => {},
  fileField: null, // (field, modalRef) => { input, getValue, pending } or null when unsupported

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
