/* Optional: form cards inside the answer, before its closing question.

   The API appends a flattened copy of each offered form to the end of the
   answer text. When the server-side half (app/inline_forms.py) finds that
   copy, it removes it and sends the answer as segments with one slot for
   form cards after the paragraph that first mentions a form. This module
   renders those segments; actions.js puts every form card for the message
   in the slot. Delete this file and inline-forms.css, plus the import in
   main.js, and cards render below the answer instead. */
import { hooks, useStylesheet } from "../hooks.js";
import { renderMarkdown } from "../markdown.js";

useStylesheet(new URL("./inline-forms.css", import.meta.url));

hooks.renderAnswer = (bubble, layout) => {
  // A re-render (a newer snapshot) must keep cards already placed.
  const placed = [...(bubble.querySelector(".inline-form-slot")?.childNodes || [])];
  bubble.replaceChildren();
  for (const segment of layout) {
    if (segment.text) {
      const part = document.createElement("div");
      part.className = "answer-part";
      renderMarkdown(part, segment.text);
      bubble.appendChild(part);
    }
    if (segment.forms && segment.forms.length) {
      const slot = document.createElement("div");
      slot.className = "inline-form-slot form-cards";
      slot.append(...placed);
      bubble.appendChild(slot);
    }
  }
};

hooks.formSlot = (turn) => turn.querySelector(".inline-form-slot");
