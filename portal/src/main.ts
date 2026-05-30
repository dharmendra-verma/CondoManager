// Tenant status portal entry point (CM-37). Wires the lookup form to the
// read-only `/api/ticket` endpoint and renders a status timeline. All pure
// logic lives in ./ticket (unit-tested); this file is DOM glue only.

import {
  type PublicTicket,
  formatEta,
  formatVendor,
  statusTimeline,
  validateCode,
} from "./ticket";

const form = document.querySelector<HTMLFormElement>("#lookup-form");
const input = document.querySelector<HTMLInputElement>("#code");
const errorEl = document.querySelector<HTMLParagraphElement>("#error");
const resultEl = document.querySelector<HTMLElement>("#result");

function showError(message: string): void {
  if (!errorEl || !resultEl) return;
  errorEl.textContent = message;
  errorEl.hidden = false;
  resultEl.hidden = true;
}

function clearError(): void {
  if (!errorEl) return;
  errorEl.hidden = true;
  errorEl.textContent = "";
}

function renderTicket(ticket: PublicTicket): void {
  if (!resultEl) return;
  const steps = statusTimeline(ticket.status)
    .map(
      (step) =>
        `<li class="step step--${step.state}"><span class="dot"></span>${step.label}</li>`,
    )
    .join("");
  resultEl.innerHTML = `
    <h2>Request ${ticket.id}</h2>
    <ol class="timeline">${steps}</ol>
    <dl class="meta">
      <dt>ETA</dt><dd>${formatEta(ticket.eta)}</dd>
      <dt>Assigned to</dt><dd>${formatVendor(ticket.vendor)}</dd>
    </dl>
  `;
  resultEl.hidden = false;
}

async function lookup(code: string): Promise<void> {
  const res = await fetch(`/api/ticket?code=${encodeURIComponent(code)}`);
  if (res.status === 404) {
    showError("No request found for that code. Double-check it and try again.");
    return;
  }
  if (!res.ok) {
    showError("Something went wrong looking that up. Please try again shortly.");
    return;
  }
  const ticket = (await res.json()) as PublicTicket;
  clearError();
  renderTicket(ticket);
}

form?.addEventListener("submit", (event) => {
  event.preventDefault();
  const code = validateCode(input?.value ?? "");
  if (!code) {
    showError("That doesn't look like a confirmation code (e.g. TKT-3F8A2C9D).");
    return;
  }
  void lookup(code);
});
