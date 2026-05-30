// Pure, framework-free helpers for the tenant status portal (CM-37).
// No DOM access here so the logic is unit-testable under vitest/node.

/** The four ticket lifecycle states, in order (mirrors CM-31 TicketStatus). */
export const TICKET_STATES = [
  "New",
  "In Progress",
  "Waiting",
  "Resolved",
] as const;

export type TicketStatus = (typeof TICKET_STATES)[number];

/** Tenant-safe ticket view returned by `GET /api/ticket` (no PII). */
export interface PublicTicket {
  id: string;
  status: TicketStatus;
  statusIndex: number;
  eta: string | null;
  vendor: string | null;
  created_at: string;
  updated_at: string;
}

/** Confirmation code shape: `TKT-` + 8 hex chars (matches CM-31 codes). */
const CODE_RE = /^TKT-[0-9A-F]{8}$/;

/**
 * Normalize + validate a user-entered confirmation code.
 * Returns the canonical upper-cased code, or `null` when invalid — the caller
 * must not hit the API for invalid input.
 */
export function validateCode(raw: string): string | null {
  const code = raw.trim().toUpperCase();
  return CODE_RE.test(code) ? code : null;
}

export type StepState = "done" | "current" | "upcoming";

export interface TimelineStep {
  label: TicketStatus;
  state: StepState;
}

/** Map a status to an ordered stepper with a single `current` marker. */
export function statusTimeline(status: TicketStatus): TimelineStep[] {
  const current = TICKET_STATES.indexOf(status);
  return TICKET_STATES.map((label, i) => ({
    label,
    state: i < current ? "done" : i === current ? "current" : "upcoming",
  }));
}

export function formatVendor(vendor: string | null): string {
  return vendor && vendor.trim() ? vendor : "Not yet assigned";
}

export function formatEta(eta: string | null): string {
  return eta && eta.trim() ? eta : "To be confirmed";
}
