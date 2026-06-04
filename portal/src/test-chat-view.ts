// DOM glue for the TEST-ONLY web chat channel (CM-55). Wiring only — all
// validation / payload / formatting logic lives in ./test-chat (unit-tested).
// Talks to the gated agents.webchat FastAPI app via the /web dev proxy.
//
// Session state is a module-level variable held in memory ONLY: no localStorage,
// no tokens. A page refresh re-runs this module with `session = null`, so the
// tenant lands back on the login view (AC: in-memory session, refresh = re-login).
//
// Bubbles are built with textContent (not innerHTML) since both the tenant's
// message and the manager reply are free text.

import {
  ChatSession,
  type ChatTurn,
  type MessageResponse,
  type TenantSession,
  loginPayload,
  messagePayload,
  validateMobile,
} from "./test-chat";

const loginView = document.querySelector<HTMLElement>("#login-view");
const chatView = document.querySelector<HTMLElement>("#chat-view");

const loginForm = document.querySelector<HTMLFormElement>("#login-form");
const mobileInput = document.querySelector<HTMLInputElement>("#mobile");
const loginError = document.querySelector<HTMLParagraphElement>("#login-error");

const whoami = document.querySelector<HTMLElement>("#whoami");
const thread = document.querySelector<HTMLElement>("#thread");
const composer = document.querySelector<HTMLFormElement>("#composer");
const messageInput = document.querySelector<HTMLInputElement>("#message");
const chatError = document.querySelector<HTMLParagraphElement>("#chat-error");
const logoutBtn = document.querySelector<HTMLButtonElement>("#logout-btn");

// The single source of truth for "are we logged in" — in memory only.
let session: ChatSession | null = null;

function setLoginError(message: string): void {
  if (!loginError) return;
  loginError.textContent = message;
  loginError.hidden = message === "";
}

function setChatError(message: string): void {
  if (!chatError) return;
  chatError.textContent = message;
  chatError.hidden = message === "";
}

function showChat(tenant: TenantSession): void {
  session = new ChatSession(tenant);
  if (whoami) {
    whoami.textContent = `${tenant.name} · Unit ${tenant.unit}`;
  }
  thread?.replaceChildren();
  if (loginView) loginView.hidden = true;
  if (chatView) chatView.hidden = false;
  messageInput?.focus();
}

function showLogin(): void {
  session = null;
  if (chatView) chatView.hidden = true;
  if (loginView) loginView.hidden = false;
  setChatError("");
  setLoginError("");
  if (loginForm) loginForm.reset();
  mobileInput?.focus();
}

function renderTurn(turn: ChatTurn): void {
  if (!thread) return;
  const bubble = document.createElement("div");
  bubble.className = `bubble bubble--${turn.role}`;
  if (turn.stub) {
    bubble.classList.add("bubble--stub");
  }
  bubble.textContent = turn.text;
  thread.appendChild(bubble);
  bubble.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function doLogin(mobile: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/web/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(loginPayload(mobile)),
    });
  } catch {
    setLoginError("Couldn't reach the web chat API. Is it running (WEBCHAT_TEST_ENABLED)?");
    return;
  }
  if (res.status === 404) {
    setLoginError("That number isn't a registered test tenant. Try another.");
    return;
  }
  if (!res.ok) {
    setLoginError("Login failed. Please try again.");
    return;
  }
  const tenant = (await res.json()) as TenantSession;
  showChat(tenant);
}

async function doSend(content: string): Promise<void> {
  if (!session) return;
  const mobileFromInput = mobileInput?.value ?? "";
  renderTurn(session.addTenant(content));
  if (messageInput) messageInput.value = "";

  let res: Response;
  try {
    res = await fetch("/web/message", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(messagePayload(mobileFromInput, content)),
    });
  } catch {
    setChatError("Couldn't reach the web chat API.");
    return;
  }
  if (!res.ok) {
    setChatError("Send failed. Please try again.");
    return;
  }
  setChatError("");
  const reply = (await res.json()) as MessageResponse;
  renderTurn(session.addManager(reply));
}

loginForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  const check = validateMobile(mobileInput?.value ?? "");
  if (!check.ok) {
    setLoginError(check.error);
    return;
  }
  setLoginError("");
  void doLogin(check.mobile);
});

composer?.addEventListener("submit", (event) => {
  event.preventDefault();
  const content = (messageInput?.value ?? "").trim();
  if (content === "") return;
  void doSend(content);
});

logoutBtn?.addEventListener("click", () => showLogin());

showLogin();
