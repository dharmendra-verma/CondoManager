import { beforeEach, describe, expect, it } from "vitest";

import type { Tenant } from "./tenant";
import { DuplicateMobileError, InMemoryTenantRepository } from "./tenantRepo";

function tenant(overrides: Partial<Tenant> = {}): Tenant {
  return {
    id: "TEN-1",
    name: "Asha Rao",
    unit: "4B",
    mobile: "+919876543210",
    email: null,
    notes: null,
    spouse: null,
    created_at: "2026-06-01T00:00:00.000Z",
    updated_at: "2026-06-01T00:00:00.000Z",
    ...overrides,
  };
}

describe("InMemoryTenantRepository", () => {
  let repo: InMemoryTenantRepository;

  beforeEach(() => {
    repo = new InMemoryTenantRepository();
  });

  it("creates then gets (hit)", async () => {
    await repo.create(tenant());
    expect(await repo.get("TEN-1")).toMatchObject({ id: "TEN-1", name: "Asha Rao" });
  });

  it("returns null on a get miss", async () => {
    expect(await repo.get("TEN-nope")).toBeNull();
  });

  it("lists all created tenants", async () => {
    await repo.create(tenant({ id: "TEN-1", mobile: "+911111111111" }));
    await repo.create(tenant({ id: "TEN-2", mobile: "+912222222222" }));
    const all = await repo.list();
    expect(all.map((t) => t.id).sort()).toEqual(["TEN-1", "TEN-2"]);
  });

  it("updates an existing tenant", async () => {
    await repo.create(tenant());
    const saved = await repo.update(tenant({ unit: "9C", updated_at: "2026-06-05T00:00:00.000Z" }));
    expect(saved).toMatchObject({ unit: "9C", updated_at: "2026-06-05T00:00:00.000Z" });
    expect(await repo.get("TEN-1")).toMatchObject({ unit: "9C" });
  });

  it("returns null when updating an unknown id", async () => {
    expect(await repo.update(tenant({ id: "TEN-ghost" }))).toBeNull();
  });

  it("removes a tenant (hit -> true, miss -> false)", async () => {
    await repo.create(tenant());
    expect(await repo.remove("TEN-1")).toBe(true);
    expect(await repo.remove("TEN-1")).toBe(false);
    expect(await repo.get("TEN-1")).toBeNull();
  });

  it("rejects a duplicate mobile on create", async () => {
    await repo.create(tenant({ id: "TEN-1", mobile: "+919999999999" }));
    await expect(
      repo.create(tenant({ id: "TEN-2", mobile: "+919999999999" })),
    ).rejects.toBeInstanceOf(DuplicateMobileError);
  });

  it("rejects a duplicate mobile on update against another tenant", async () => {
    await repo.create(tenant({ id: "TEN-1", mobile: "+911111111111" }));
    await repo.create(tenant({ id: "TEN-2", mobile: "+912222222222" }));
    await expect(
      repo.update(tenant({ id: "TEN-2", mobile: "+911111111111" })),
    ).rejects.toBeInstanceOf(DuplicateMobileError);
  });

  it("allows updating a tenant while keeping its own mobile", async () => {
    await repo.create(tenant({ id: "TEN-1", mobile: "+911111111111" }));
    const saved = await repo.update(tenant({ id: "TEN-1", mobile: "+911111111111", unit: "12A" }));
    expect(saved).toMatchObject({ unit: "12A" });
  });
});
