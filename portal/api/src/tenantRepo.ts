// Tenant data access for the admin CRUD API (CM-56). Mirrors the project's
// repository pattern — interface + in-memory + Cosmos + env-keyed factory (see
// agents/maintenance/repository.py) — and the Cosmos wiring in cosmos.ts.
//
// The `tenants` container uses partition key `/id`, so point reads/writes pass
// the id as both item id and partition key. Mobile uniqueness is enforced in
// app code (Cosmos has no cross-document unique constraint here) — cheap at
// MVP volume, same SELECT * scan approach as cosmos.ts.

import { type Container, CosmosClient } from "@azure/cosmos";

import { type Tenant, toTenant } from "./tenant";

const DATABASE = "condomanager";
const CONTAINER = "tenants";
const PLACEHOLDER = "REPLACE-ME";

/** Thrown when a create/update would duplicate an existing tenant `mobile`. */
export class DuplicateMobileError extends Error {
  constructor(mobile: string) {
    super(`a tenant with mobile ${mobile} already exists`);
    this.name = "DuplicateMobileError";
  }
}

/** Minimal persistence surface the tenant admin API needs. */
export interface TenantRepository {
  list(): Promise<Tenant[]>;
  get(id: string): Promise<Tenant | null>;
  /** Persist a new tenant. Throws `DuplicateMobileError` on a mobile clash. */
  create(tenant: Tenant): Promise<Tenant>;
  /**
   * Replace an existing tenant. Returns `null` when `tenant.id` is unknown,
   * throws `DuplicateMobileError` when the mobile collides with another id.
   */
  update(tenant: Tenant): Promise<Tenant | null>;
  /** Returns `true` if a tenant was deleted, `false` when the id was unknown. */
  remove(id: string): Promise<boolean>;
}

/** Process-local store for tests and offline dev. */
export class InMemoryTenantRepository implements TenantRepository {
  private readonly byId = new Map<string, Tenant>();

  async list(): Promise<Tenant[]> {
    return [...this.byId.values()];
  }

  async get(id: string): Promise<Tenant | null> {
    return this.byId.get(id) ?? null;
  }

  async create(tenant: Tenant): Promise<Tenant> {
    this.assertMobileFree(tenant.mobile, tenant.id);
    this.byId.set(tenant.id, tenant);
    return tenant;
  }

  async update(tenant: Tenant): Promise<Tenant | null> {
    if (!this.byId.has(tenant.id)) {
      return null;
    }
    this.assertMobileFree(tenant.mobile, tenant.id);
    this.byId.set(tenant.id, tenant);
    return tenant;
  }

  async remove(id: string): Promise<boolean> {
    return this.byId.delete(id);
  }

  private assertMobileFree(mobile: string, selfId: string): void {
    for (const existing of this.byId.values()) {
      if (existing.mobile === mobile && existing.id !== selfId) {
        throw new DuplicateMobileError(mobile);
      }
    }
  }
}

/** Cosmos-backed repository over the `tenants` container. */
export class CosmosTenantRepository implements TenantRepository {
  private container: Container | null = null;

  private getContainer(): Container {
    if (this.container !== null) {
      return this.container;
    }
    const conn = (process.env.COSMOS_CONNECTION_STRING ?? "").trim();
    if (conn === "" || conn === PLACEHOLDER) {
      throw new Error("COSMOS_CONNECTION_STRING is not configured");
    }
    const client = new CosmosClient(conn);
    this.container = client.database(DATABASE).container(CONTAINER);
    return this.container;
  }

  async list(): Promise<Tenant[]> {
    const { resources } = await this.getContainer()
      .items.query<Record<string, unknown>>({ query: "SELECT * FROM c" })
      .fetchAll();
    return resources
      .map((doc) => toTenant(doc))
      .filter((t): t is Tenant => t !== null);
  }

  async get(id: string): Promise<Tenant | null> {
    try {
      const { resource } = await this.getContainer()
        .item(id, id)
        .read<Record<string, unknown>>();
      return resource ? toTenant(resource) : null;
    } catch (err) {
      if ((err as { code?: number }).code === 404) {
        return null;
      }
      throw err;
    }
  }

  async create(tenant: Tenant): Promise<Tenant> {
    await this.assertMobileFree(tenant.mobile, tenant.id);
    await this.getContainer().items.create(tenant);
    return tenant; // the persisted doc equals what we sent (plus system fields)
  }

  async update(tenant: Tenant): Promise<Tenant | null> {
    if ((await this.get(tenant.id)) === null) {
      return null;
    }
    await this.assertMobileFree(tenant.mobile, tenant.id);
    await this.getContainer().item(tenant.id, tenant.id).replace(tenant);
    return tenant;
  }

  async remove(id: string): Promise<boolean> {
    try {
      await this.getContainer().item(id, id).delete();
      return true;
    } catch (err) {
      if ((err as { code?: number }).code === 404) {
        return false;
      }
      throw err;
    }
  }

  private async assertMobileFree(mobile: string, selfId: string): Promise<void> {
    const { resources } = await this.getContainer()
      .items.query<{ id: string }>({
        query: "SELECT c.id FROM c WHERE c.mobile = @mobile",
        parameters: [{ name: "@mobile", value: mobile }],
      })
      .fetchAll();
    if (resources.some((r) => r.id !== selfId)) {
      throw new DuplicateMobileError(mobile);
    }
  }
}

let cached: TenantRepository | null = null;

/**
 * Return the process-wide tenant repository. `COSMOS_CONNECTION_STRING` unset /
 * blank / `REPLACE-ME` -> in-memory (tests + offline dev); otherwise Cosmos.
 * Mirrors `get_ticket_repository()` in the agents package.
 */
export function getTenantRepository(): TenantRepository {
  if (cached !== null) {
    return cached;
  }
  const conn = (process.env.COSMOS_CONNECTION_STRING ?? "").trim();
  cached =
    conn === "" || conn === PLACEHOLDER
      ? new InMemoryTenantRepository()
      : new CosmosTenantRepository();
  return cached;
}

/** Test hook: drop the cached repository so the next call rebuilds it. */
export function resetTenantRepository(): void {
  cached = null;
}
