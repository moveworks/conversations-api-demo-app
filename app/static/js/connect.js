import { getJson, postJson } from "./api.js";
import { $, emit } from "./dom.js";
import { state } from "./state.js";
import { chatLog } from "./chat.js";
import { loadConversations } from "./conversations.js";
import { closeNotifications, connectNotifications } from "./notifications.js";

export async function boot() {
  const session = await getJson("/api/session");
  const select = $("base-url-select");
  for (const url of session.known_base_urls) select.appendChild(new Option(url, url));
  select.appendChild(new Option("Custom base URL…", "__custom__"));
  select.addEventListener("change", () => {
    $("base-url-custom").hidden = select.value !== "__custom__";
  });
  if (session.connected) enterApp(session.bot_name, session.base_url);
}

export async function connect() {
  const select = $("base-url-select");
  const baseUrl = select.value === "__custom__" ? $("base-url-custom").value : select.value;
  const btn = $("connect-btn");
  const err = $("connect-error");
  err.hidden = true;
  btn.disabled = true;
  btn.textContent = "Connecting…";
  try {
    const body = await postJson("/api/connect", {
      base_url: baseUrl,
      bot_name: $("bot-name").value,
      api_key: $("api-key").value,
    }, "Connection failed.");
    $("api-key").value = "";
    enterApp(body.bot_name, body.base_url);
  } catch (e) {
    err.textContent = String(e.message || e);
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect";
  }
}

function enterApp(botName, baseUrl) {
  $("connect-screen").hidden = true;
  $("app").hidden = false;
  $("bot-chip").textContent = botName;
  $("org-chip").textContent = baseUrl.replace("https://", "");
  connectNotifications();
  loadConversations();
  $("chat-input").focus();
  emit("app:connected");
}

export async function disconnect() {
  await postJson("/api/disconnect", {});
  closeNotifications();
  state.conversationId = null;
  chatLog.replaceChildren();
  $("chat-empty").hidden = false;
  $("app").hidden = true;
  $("connect-screen").hidden = false;
  emit("app:disconnected");
}
