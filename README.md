# Moveworks Conversations API: demo app

A demo chat surface for the [Moveworks Conversations API](https://docs.moveworks.com/api-reference/conversations-api), with streamed replies and a live reasoning trail, form cards inside the answer, motion, a protocol inspector, dark mode, and a panel that sends you a test proactive notification. Every API call goes through the `moveworks_capi` library from the [Conversations API starter](https://github.com/moveworks/conversations-api-starter), which this repo installs as a dependency.

To build your own integration, fork the [starter](https://github.com/moveworks/conversations-api-starter). Its reference app covers the same API features with plain styling.

> [!NOTE]
> The Conversations API is in **Controlled Availability**. Endpoints, behaviors, and configuration surfaces may change during CA. Contact your Moveworks account team to participate.

---

## Run it

Requires Python 3.10 or later and a Moveworks instance with the Conversations API enabled, a [Conversations API chatbot](https://docs.moveworks.com/api-reference/conversations-api/create-the-chatbot), and an HTTP Connector API key (see [API Credentials](https://docs.moveworks.com/api-reference/api-credentials)). Confirm with your Moveworks account team that the Conversations API accepts an API Key for your Moveworks instance.

1. Clone the repo and change into it.

   ```bash
   git clone https://github.com/moveworks/conversations-api-demo-app.git
   cd conversations-api-demo-app
   ```

2. Confirm your Python version, then create a virtual environment and activate it. The app requires Python 3.10 or later.

   ```bash
   python3 --version
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   On an older Python, the install in the next step fails with `File "setup.py" or "setup.cfg" not found`. Install Python 3.10 or later and create the virtual environment with it.

3. Install the app as an editable install. This also installs the starter library from its `v0.1.0` tag, plus FastAPI and uvicorn.

   ```bash
   pip install -e .
   ```

4. Optional: to use the test notifications panel, copy the environment template and fill in the listener values (see [Testing proactive notifications](#testing-proactive-notifications)). Nothing else reads `.env`.

   ```bash
   cp .env.example .env
   ```

5. Start the app, then open http://127.0.0.1:8090.

   ```bash
   python -m app                 # stream replies
   python -m app --no-stream     # submit and poll instead of streaming
   python -m app --port 9000     # serve on another port
   ```

6. On the connect screen, choose a data center and paste the chatbot's Bot Name and an HTTP Connector API key. The server holds the key in memory only, never on disk or in browser storage.

The app runs locally with one connected Moveworks instance per process. It binds to `127.0.0.1`, has no login, and rejects requests with a non-local `Host`, cross-origin writes, and non-JSON writes, so other websites cannot drive it through your browser. Do not expose it on a network.

To run the offline tests:

```bash
pip install -e ".[test]"
python -m pytest
```

---

## Design choices

Each behavior below is an optional module. To remove one, delete the listed files and its import line in `app/static/js/main.js`; the app falls back to the plain behavior.

| Module | What it adds | Without it | Delete |
|---|---|---|---|
| **Streaming** | Replies arrive progressively over server-sent events; the reasoning trail updates live, then collapses as the answer starts. | Submit the message, poll until complete, render the answer at once. `--no-stream` does the same at runtime. | `app/streaming.py`, `app/static/js/optional/thinking.js` + `.css` |
| **Inline forms** | Removes the plain-text copy of offered forms from the answer (see `rendering.layout_message` in the starter) and places the form cards as one group after the paragraph that first mentions a form, before the closing question. | The answer renders as sent, including the plain-text copy, and form cards render below the answer. | `app/inline_forms.py`, `app/static/js/optional/inline-forms.js` + `.css` |
| **Motion** | Tweens height changes so large answer snapshots glide instead of jumping, keeps the view pinned to the bottom while they run, fades new blocks in, staggers form cards. Respects `prefers-reduced-motion`. | Every change applies instantly. | `app/static/js/optional/motion.js` + `.css` |
| **Wire pane** | A side pane that logs every API call and stream event. | No pane, no Wire button. | `app/static/js/optional/wire.js` + `.css` |
| **Test notifications** | A panel at the top of the notifications drawer that sends you a proactive notification through an Agent Studio listener, then times its round trip back through the events API. Needs `MW_TEST_LISTENER_URL` (and `MW_TEST_LISTENER_SECRET` for a secured listener) in `.env`; see "Testing proactive notifications" below. | No panel; notifications arrive only when something else triggers them. | `app/test_notifications.py`, `app/static/js/optional/test-notify.js` + `.css` |

---

## Testing proactive notifications

To send a test notification on demand, use an Agent Studio listener and a plugin that ends in a `notify` step. Build them once per test Moveworks instance:

1. **Compound action** with inputs `email` and `message`: a `mw.get_user_by_email` step, then `notify` with `recipient_id` set to that user's id and `message` set to the `message` input.
2. **Listener** with signature verification: HMAC-SHA256 over the raw body, read from the `X-Signature` header, with a secret you generate. Unsecured listeners also work, limited to one request per 10 seconds per Moveworks instance.
3. **Plugin** with a system trigger on that listener, mapping `parsed_body.email` and `parsed_body.message` into the compound action.
4. Put the listener URL (`https://api.moveworks.ai/webhooks/v1/listeners/{url id}/notify`), the secret, and your email in `.env` as `MW_TEST_LISTENER_URL`, `MW_TEST_LISTENER_SECRET`, and `MW_TEST_NOTIFY_EMAIL`.

Then send a chat message in the app (delivery goes to the channel you used most recently), open the notifications drawer, and press Send. The panel reports the round trip time. A notification usually arrives in a new conversation titled "System Initiated Conversation". A secured listener accepts the request before verifying the signature, so a rejected signature appears only in the listener's logs in Agent Studio.

---

## Versioning

This repo is versioned 0.x and pins the starter to a tag in `pyproject.toml`. Breaking changes can land in minor versions. [CHANGELOG.md](CHANGELOG.md) records each release.

## Support

See [SUPPORT.md](SUPPORT.md). Security reports follow [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE.txt). This repository is a demo, provided as-is. It is not a supported Moveworks product and carries no SLA. Verify against the [Conversations API documentation](https://docs.moveworks.com/api-reference/conversations-api) before adopting anything from it.
