# Changelog

All notable changes to this project are recorded here. The project is versioned 0.x, and breaking changes can land in minor versions.

## Unreleased

- Messages sent while a reply is still arriving are queued and sent in order, instead of locking the composer. Switching or starting a conversation, or disconnecting, cancels the reply in progress and clears the queue. If a send fails, the messages still waiting are marked "Not sent".
- The page and static files are served with `Cache-Control: no-cache`, so a browser picks up edited JavaScript and CSS on reload.
- A `HANDOFF_ACTION` opens the message's `Message.handoff` menu from its place in the actions row, and is omitted when the message has no handoff options. It was shown as an unsupported action.
- The live agent avatar from `sender_info.avatar_url` is shown only for an `https` URL and is loaded with no referrer.

## 0.1.0

First tagged release.

- Demo chat surface over the Moveworks Conversations API, built on [`moveworks-capi-starter`](https://github.com/moveworks/conversations-api-starter) 0.1.0.
- Optional modules: streaming with a live reasoning trail, inline form cards, motion, a protocol wire pane, and a test-notifications panel.
- Offline test suite, run in CI.
