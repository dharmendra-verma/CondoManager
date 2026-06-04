// Regression for CM-61: the Functions host loads only the entry module named by
// package.json "main" (dist/src/index.js). Any function whose app.http(...)
// registration lives in another module must be reachable from this entry point,
// or its route is never registered and the SWA front door returns a bare 404.
//
// We mock @azure/functions so importing ./index captures every registered route
// name instead of touching the real host. Before the fix index.ts registered
// only "ticket"; the tenant admin routes were dark.

import { describe, expect, it, vi } from "vitest";

const registered: string[] = [];

vi.mock("@azure/functions", () => ({
  app: {
    http: (name: string) => {
      registered.push(name);
    },
  },
}));

describe("portal API entry point (index.ts)", () => {
  it("registers the ticket and tenant admin routes at startup", async () => {
    await import("./index");

    expect(registered).toContain("ticket");
    // The /api/admin/tenants CRUD surface (CM-56) must be wired in via the
    // entry point — regression guard for CM-61.
    expect(registered).toContain("tenantsCollection");
    expect(registered).toContain("tenantItem");
  });
});
