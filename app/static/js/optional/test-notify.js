/* Optional: send yourself a proactive notification, then watch it arrive.

   The server half (app/test_notifications.py) POSTs to a Tool Studio
   listener whose plugin notifies the recipient; the message then comes back
   through the events polling the app already does. This form sits, collapsed,
   under the inbox on the Notifications tab and times each round trip. Delete this
   file and test-notify.css, plus the import in main.js, to drop it. */
import { $ } from "../dom.js";
import { getJson, postJson } from "../api.js";
import { hooks, useStylesheet } from "../hooks.js";

useStylesheet(new URL("./test-notify.css", import.meta.url));

const section = document.createElement("section");
section.className = "test-notify";
const opener = document.createElement("button");
opener.type = "button";
opener.className = "tn-toggle disclosure";
opener.textContent = "Send a test notification";
opener.setAttribute("aria-expanded", "false");
const panel = document.createElement("form");
panel.className = "tn-form";
panel.id = "test-notify-form";
panel.hidden = true;
opener.setAttribute("aria-controls", panel.id);
opener.addEventListener("click", () => {
  panel.hidden = !panel.hidden;
  opener.setAttribute("aria-expanded", String(!panel.hidden));
  if (!panel.hidden) refresh();
});
panel.innerHTML = `
  <p class="tn-warn" hidden></p>
  <label class="tn-field"><span>Recipient</span><input name="email" type="email" autocomplete="off" required></label>
  <label class="tn-field"><span>Message</span><textarea name="message" rows="2" maxlength="2000" required></textarea></label>
  <div class="tn-row">
    <select name="delay" aria-label="Delay">
      <option value="0">Send now</option>
      <option value="10">In 10 seconds</option>
      <option value="30">In 30 seconds</option>
    </select>
    <button type="submit" class="primary small">Send</button>
  </div>
  <p class="tn-status" aria-live="polite"></p>`;
section.append(opener, panel);
$("notif-list").after(section);

const warn = panel.querySelector(".tn-warn");
const status = panel.querySelector(".tn-status");
const sendBtn = panel.querySelector("button[type=submit]");
const pending = [];

function defaultMessage() {
  return `Test notification from the demo app · ${new Date().toLocaleTimeString()}`;
}

async function refresh() {
  let info;
  try { info = await getJson("/api/test-notification"); } catch { return; }
  if (!info.configured) {
    section.classList.add("unconfigured");
    warn.hidden = false;
    warn.textContent = "Set MW_TEST_LISTENER_URL in .env (a Tool Studio listener that notifies the recipient), then restart the app.";
    sendBtn.disabled = true;
    return;
  }
  section.classList.remove("unconfigured");
  sendBtn.disabled = false;
  if (!panel.email.value) panel.email.value = info.email || "";
  if (!panel.message.value) panel.message.value = defaultMessage();
  // Delivery goes only to the channel the user used most recently; if that
  // was Slack or Teams, the Conversations API never receives it.
  warn.hidden = info.seconds_since_last_message !== null;
  warn.textContent = "Send a chat message from this app first. Notifications go only to the channel you used most recently, so if that was Slack or Teams this one never reaches the Conversations API.";
}

panel.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = panel.message.value.trim();
  const delay = Number(panel.delay.value);
  sendBtn.disabled = true;
  status.textContent = delay ? `Sending in ${delay}s…` : "Sending…";
  hooks.wire({ dir: "send", label: "POST listener (test notification)", detail: message.slice(0, 80) });
  try {
    const { sent_at: sentAt } = await postJson("/api/test-notification", {
      email: panel.email.value.trim(),
      message,
      delay_seconds: delay,
    }, "could not send");
    const entry = { text: normalize(message), sentAt: sentAt * 1000 };
    pending.push(entry);
    status.textContent = "Sent to the listener. Waiting for it to come back through the events API…";
    setTimeout(() => {
      if (!pending.includes(entry)) return;
      // A secured listener accepts the request before verifying the signature,
      // so a rejected signature appears only in the listener's logs.
      status.textContent = "Nothing after 45 seconds. Either Slack or Teams was used more recently, so the notification went there, or the listener rejected the signature. Check the listener's logs in Tool Studio and MW_TEST_LISTENER_SECRET in .env.";
    }, 45000);
    panel.message.value = defaultMessage();
  } catch (err) {
    status.textContent = String(err.message || err);
  } finally {
    sendBtn.disabled = false;
  }
});

hooks.notificationArrived = (data, element) => {
  const text = normalize(data.text || "");
  const i = pending.findIndex((p) => text.includes(p.text));
  if (i === -1) return;
  const { sentAt } = pending.splice(i, 1)[0];
  const seconds = ((Date.now() - sentAt) / 1000).toFixed(1);
  const where = element.classList.contains("turn") ? "in the open conversation" : "on the Notifications tab";
  status.textContent = `Arrived after ${seconds}s, ${where} (conversation ${data.conversation_id || "unknown"}).`;
  const badge = document.createElement("span");
  badge.className = "tn-badge";
  badge.textContent = `test · ${seconds}s`;
  element.appendChild(badge);
};

function normalize(text) {
  return text.replace(/\s+/g, " ").trim();
}

document.addEventListener("rail:change", (e) => { if (e.detail.active === "notifications") refresh(); });
refresh();
