# Changelog

All notable changes to this project are recorded here. The project is versioned 0.x, and breaking changes can land in minor versions.

## Unreleased

- Messages sent while a reply is still arriving are queued and sent in order, instead of locking the composer. Switching or starting a conversation, or disconnecting, cancels the reply in progress and clears the queue. If a send fails, the messages still waiting are marked "Not sent".
- Optional attachments module: attach files from the composer (button, drag and drop, or paste) or in modal FILE fields. Each file uploads through the Files API when picked and shows as a chip with its size, progress, and any error from the API; Send waits for uploads to finish, and the turn carries up to five files as `chat.files`. The app's server refuses files over 100 MB while reading them, before forwarding, and the Wire tab shows the upload with file parts replaced by their name and size. The Files API is in alpha and enabled per Moveworks instance.
- The whole UI follows one `--font-scale` setting in `app/static/styles.css` (1.15 by default, 1 below 860 px wide): text, spacing, and panel widths are in `rem`.
- Notifications and Wire share one side panel with a tab for each. It sits beside the chat, below the top bar, so it never covers the chat or the top bar's buttons; drag its left edge (or focus it and use the arrow keys) to resize it, and Escape closes it. On narrow screens the panel and the conversations list open over the chat behind a scrim.
- The top bar holds Notifications, a More menu (dark mode, Disconnect), and Wire when the wire module is loaded. The status dot follows the events stream: green while connected, amber while reconnecting, red when the stream is closed.
- Wire rows show the method, the path with ids as `{id}`, a colored status, and the time taken; expanding a row shows the full URL. Request and response are labeled and marked by color. Bodies wrap instead of scrolling sideways, and each wrapped line stays indented under its own line, so nesting still reads. "Hide polls" (on by default) hides the repeating event polls, and "Follow" scrolls to new rows while the list is at the bottom. The Wire tab uses a light code palette in light mode and the dark console palette in dark mode.
- Form cards read their modals two at a time, and only for turns on screen, with one retry, so every form offer in a long conversation renders as a card.
- A modal that opens with a header shows that header as its title, with the modal's own title as a small label above it. Modals have a Cancel button.
- Sources sit directly under the answer. Feedback buttons are shown on the newest assistant message, and on hover or focus elsewhere.
- The empty conversation offers starter prompts you can click.
- A `FAILED` response shows its own message when it carries one, and the generic "could not complete" banner only when it does not.
- "Clear list" at the bottom of the sidebar hides every conversation listed so far, in this browser only (nothing is archived or deleted in Moveworks); newer conversations still appear, and "Show cleared" brings the rest back.
- The test notification form is collapsed under the inbox on the Notifications tab.
- Conversations and clickable notifications can be opened from the keyboard.
- The page and static files are served with `Cache-Control: no-cache`, so a browser picks up edited JavaScript and CSS on reload.
- A `HANDOFF_ACTION` opens the message's `Message.handoff` menu from its place in the actions row, and is omitted when the message has no handoff options. It was shown as an unsupported action.
- The live agent avatar from `sender_info.avatar_url` is shown only for an `https` URL and is loaded with no referrer.
- Built on `moveworks-capi-starter` 0.3.0 (Files API upload for the attachments module): consent links are recognized by `url_action.purpose`, with the `/connections` URL check as a fallback when `purpose` is absent or unknown.
- A `URL_ACTION` whose URL is not `http` or `https`, including one inside a handoff item, is shown as unsupported instead of as a link. Citations and modal images and downloads with such a URL are shown without a link.
- Handoff items with no action or text, and categories left empty, are dropped, so an empty Get Help menu is never offered.
- Wire pane rows expand to the full request and response of each API call: method, URL, headers (collapsed until clicked), bodies, status, and the raw event stream of a streamed reply. Calls made outside a chat turn, such as event polling, history, and modals, appear too, and repeated event polls fold into one row. Credentials are redacted on the server, and bodies over 64 KB are truncated. Capture needs `app/wire_capture.py` on the server and starts when the wire pane loads, so with the pane deleted the server records nothing.
- The submit-and-poll path (`--no-stream`) logs its `POST /responses` and polling calls in the wire pane.
- Writes are accepted only from the app's own origin, including its port, so a page served from another local port cannot drive the app.
- A consent link whose label already starts with "Connect" is not prefixed again. After consent, the toast no longer asks for another message, since the original request resumes on its own and its result arrives in the conversation.

## 0.1.0

First tagged release.

- Demo chat surface over the Moveworks Conversations API, built on [`moveworks-capi-starter`](https://github.com/moveworks/conversations-api-starter) 0.1.0.
- Optional modules: streaming with a live reasoning trail, inline form cards, motion, a protocol wire pane, and a test-notifications panel.
- Offline test suite, run in CI.
