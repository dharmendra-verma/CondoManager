import { describe, expect, it } from "vitest";

import { type PublicTicket, toPublicTicket } from "./shape";

// A realistic raw Cosmos ticket doc (CM-31 shape) including PII fields that
// MUST NOT appear in the public response.
const RAW_DOC: Record<string, unknown> = {
  id: "TKT-3F8A2C9D",
  tenant_id: "tenant-007",
  tenantId: "tenant-007",
  unit: "4B",
  issue_text: "Kitchen sink leaking under the cabinet",
  category: "plumbing",
  priority: "P2",
  status: "In Progress",
  owner: "Ace Plumbing",
  created_at: "2026-05-20T10:00:00+00:00",
  updated_at: "2026-05-21T09:30:00+00:00",
  request_id: "req_abc",
  duplicate_of: null,
  eta: "within 2 hours",
  history: ["created", "assigned"],
  _etag: "\"sys\"",
};

describe("toPublicTicket", () => {
  it("maps the tenant-safe fields", () => {
    const pub = toPublicTicket(RAW_DOC);
    expect(pub).toEqual<PublicTicket>({
      id: "TKT-3F8A2C9D",
      status: "In Progress",
      statusIndex: 1,
      eta: "within 2 hours",
      vendor: "Ace Plumbing",
      created_at: "2026-05-20T10:00:00+00:00",
      updated_at: "2026-05-21T09:30:00+00:00",
    });
  });

  it("never leaks PII / internal fields", () => {
    const pub = toPublicTicket(RAW_DOC);
    expect(pub).not.toBeNull();
    const keys = Object.keys(pub as PublicTicket);
    for (const banned of [
      "tenant_id",
      "tenantId",
      "unit",
      "issue_text",
      "category",
      "priority",
      "request_id",
      "duplicate_of",
      "history",
      "_etag",
    ]) {
      expect(keys).not.toContain(banned);
    }
  });

  it("nulls missing eta / vendor rather than inventing them", () => {
    const pub = toPublicTicket({ ...RAW_DOC, eta: undefined, owner: undefined });
    expect(pub?.eta).toBeNull();
    expect(pub?.vendor).toBeNull();
  });

  it("returns null for an invalid/unknown status", () => {
    expect(toPublicTicket({ ...RAW_DOC, status: "Closed" })).toBeNull();
  });

  it("returns null when id is missing", () => {
    const { id: _id, ...noId } = RAW_DOC;
    expect(toPublicTicket(noId)).toBeNull();
  });
});
