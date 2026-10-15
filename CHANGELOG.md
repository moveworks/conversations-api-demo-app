# Changelog

All notable changes to this project are recorded here. The project is versioned 0.x, and breaking changes can land in minor versions.

## Unreleased

- Messages sent while a reply is still arriving are queued and sent in order, instead of locking the composer. Switching or starting a conversation, or disconnecting, cancels the reply in progress and clears the queue. If a send fails, the messages still waiting are marked "Not sent".
- The page and static files are served with `Cache-Control: no-cache`, so a browser picks up edited JavaScript and CSS on reload.
- A `HANDOFF_ACTION` opens the message's `Message.handoff` menu from its place in the actions row, and is omitted when the message has no handoff options. It was shown as an unsupported action.
- The live agent avatar from `sender_info.avatar_url` is shown only for an `https` URL and is loaded with no referrer.
- Built on `moveworks-capi-starter` 0.2.0: consent links are recognized by `url_action.purpose`, with the `/connections` URL check as a fallback when `purpose` is absent or unknown.
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
