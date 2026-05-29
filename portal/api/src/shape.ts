// Pure ticket → public-view shaping (CM-37). No Azure imports so it is
// unit-testable under vitest without the Functions/Cosmos SDKs.
//
// SECURITY: the confirmation-code lookup is unauthenticated, so the response
// MUST NOT echo PII. This module is an explicit WHITELIST — `issue_text`,
// `unit`, `tenant_id`/`tenantId`, `request_id`, `category`, `priority`,
// `duplicate_of`, and `history` are never copied into the public shape.

export const TICKET_STATES = [
  "New",
  "In Progress",
  "Waiting",
  "Resolved",
] as const;

export type TicketStatus = (typeof TICKET_STATES)[number];

export interface PublicTicket {
  id: string;
  status: TicketStatus;
  statusIndex: number;
  eta: string | null;
  vendor: string | null;
  created_at: string;
  updated_at: string;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function isStatus(value: unknown): value is TicketStatus {
  return (
    typeof value === "string" && (TICKET_STATES as readonly string[]).includes(value)
  );
}

/**
 * Project a raw Cosmos `tickets` document into the tenant-safe public view.
 * Returns `null` when the doc lacks a valid id/status (treated as not-found by
 * the caller) — never throws, never leaks unexpected fields.
 */
export function toPublicTicket(doc: Record<string, unknown>): PublicTicket | null {
  const id = asString(doc.id);
  if (id === null || !isStatus(doc.status)) {
    return null;
  }
  return {
    id,
    status: doc.status,
    statusIndex: TICKET_STATES.indexOf(doc.status),
    eta: asString(doc.eta),
    vendor: asString(doc.owner), // `owner` is the assigned vendor
    created_at: asString(doc.created_at) ?? "",
    updated_at: asString(doc.updated_at) ?? "",
  };
}
