import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { HttpRequest, InvocationContext } from "@azure/functions";

import type { Tenant } from "./tenant";
import { resetTenantRepository } from "./tenantRepo";
import { tenantItemHandler, tenantsCollectionHandler } from "./tenants";

// Silent context; the handlers only call ctx.error on dependency failures.
const ctx = { error: () => {} } as unknown as InvocationContext;

function makeReq(opts: {
  method: string;
  body?: unknown;
  params?: Record<string, string>;
}): HttpRequest {
  return {
    method: opts.method,
    params: opts.params ?? {},
    json: async () => {
      if (opts.body === undefined) {
        throw new Error("no body");
      }
      return opts.body;
    },
  } as unknown as HttpRequest;
}

const VALID = { name: "Asha Rao", unit: "4B", mobile: "+919876543210" };

async function createTenant(body: object = VALID): Promise<Tenant> {
  const res = await tenantsCollectionHandler(makeReq({ method: "POST", body }), ctx);
  expect(res.status).toBe(201);
  return res.jsonBody as Tenant;
}

describe("tenant admin endpoints", () => {
  beforeEach(() => {
    // In-memory repo (no Cosmos), flag on, fresh store per test.
    delete process.env.COSMOS_CONNECTION_STRING;
    process.env.TENANT_ADMIN_ENABLED = "1";
    resetTenantRepository();
  });

  afterEach(() => {
    delete process.env.TENANT_ADMIN_ENABLED;
    resetTenantRepository();
  });

  describe("when the dev/test flag is off", () => {
    beforeEach(() => {
      delete process.env.TENANT_ADMIN_ENABLED;
    });

    it("hides the collection route (404)", async () => {
      const res = await tenantsCollectionHandler(makeReq({ method: "GET" }), ctx);
      expect(res.status).toBe(404);
    });

    it("hides the item route (404)", async () => {
      const res = await tenantItemHandler(makeReq({ method: "GET", params: { id: "TEN-1" } }), ctx);
      expect(res.status).toBe(404);
    });
  });

  it("creates a tenant (201) with a generated id + timestamps", async () => {
    const created = await createTenant();
    expect(created.id).toMatch(/^TEN-/);
    expect(created.created_at).not.toBe("");
    expect(created.updated_at).toBe(created.created_at);
    expect(created).toMatchObject(VALID);
  });

  it("rejects an invalid create body (400 with field errors)", async () => {
    const res = await tenantsCollectionHandler(
      makeReq({ method: "POST", body: { name: "", unit: "", mobile: "x" } }),
      ctx,
    );
    expect(res.status).toBe(400);
    expect((res.jsonBody as { error: string }).error).toBe("validation_failed");
  });

  it("rejects a duplicate mobile on create (409)", async () => {
    await createTenant();
    const res = await tenantsCollectionHandler(
      makeReq({ method: "POST", body: { ...VALID, unit: "5C" } }),
      ctx,
    );
    expect(res.status).toBe(409);
    expect((res.jsonBody as { error: string }).error).toBe("duplicate_mobile");
  });

  it("lists tenants (200)", async () => {
    await createTenant();
    const res = await tenantsCollectionHandler(makeReq({ method: "GET" }), ctx);
    expect(res.status).toBe(200);
    expect(res.jsonBody as Tenant[]).toHaveLength(1);
  });

  it("gets a tenant by id (200) and 404s a miss", async () => {
    const created = await createTenant();
    const hit = await tenantItemHandler(
      makeReq({ method: "GET", params: { id: created.id } }),
      ctx,
    );
    expect(hit.status).toBe(200);
    const miss = await tenantItemHandler(
      makeReq({ method: "GET", params: { id: "TEN-nope" } }),
      ctx,
    );
    expect(miss.status).toBe(404);
  });

  it("updates a tenant (200) preserving id + created_at", async () => {
    const created = await createTenant();
    const res = await tenantItemHandler(
      makeReq({ method: "PUT", params: { id: created.id }, body: { ...VALID, unit: "9Z" } }),
      ctx,
    );
    expect(res.status).toBe(200);
    const saved = res.jsonBody as Tenant;
    expect(saved).toMatchObject({ id: created.id, unit: "9Z", created_at: created.created_at });
  });

  it("404s an update to an unknown id", async () => {
    const res = await tenantItemHandler(
      makeReq({ method: "PUT", params: { id: "TEN-ghost" }, body: VALID }),
      ctx,
    );
    expect(res.status).toBe(404);
  });

  it("rejects an update that collides with another tenant's mobile (409)", async () => {
    const a = await createTenant({ name: "A", unit: "1", mobile: "+911111111111" });
    await createTenant({ name: "B", unit: "2", mobile: "+912222222222" });
    const res = await tenantItemHandler(
      makeReq({ method: "PUT", params: { id: a.id }, body: { name: "A", unit: "1", mobile: "+912222222222" } }),
      ctx,
    );
    expect(res.status).toBe(409);
  });

  it("deletes a tenant (204) and 404s a missing delete", async () => {
    const created = await createTenant();
    const del = await tenantItemHandler(
      makeReq({ method: "DELETE", params: { id: created.id } }),
      ctx,
    );
    expect(del.status).toBe(204);
    const again = await tenantItemHandler(
      makeReq({ method: "DELETE", params: { id: created.id } }),
      ctx,
    );
    expect(again.status).toBe(404);
  });
});
