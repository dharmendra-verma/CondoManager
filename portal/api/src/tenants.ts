// SWA managed Functions: tenant master CRUD for the admin page (CM-56).
// Registered via the @azure/functions v4 programming model.
//
// TODO(auth): these endpoints are UNAUTHENTICATED and serve tenant PII. Two
// guards keep them off real environments until real auth lands: (1) they sit
// on the `/api/admin/*` route, separate from the anonymous public
// `/api/ticket` portal; (2) every handler is gated behind the
// `TENANT_ADMIN_ENABLED` dev/test flag, which is OFF by default — so the route
// is invisible (404) in staging/prod. Real auth (SWA roles / AAD) MUST land
// before any real deployment — see CM-56 "Security (deferred)".

import { randomUUID } from "node:crypto";

import {
  type HttpRequest,
  type HttpResponseInit,
  type InvocationContext,
} from "@azure/functions";

import { type Tenant, validateTenantInput } from "./tenant";
import { DuplicateMobileError, getTenantRepository } from "./tenantRepo";

// Opt-in truthy values. Anything else (incl. unset, "0", "false") is disabled.
const ENABLED_VALUES = new Set(["1", "true", "yes"]);

/**
 * Whether the tenant admin API is enabled. Opt-in: only `TENANT_ADMIN_ENABLED`
 * set to a truthy value turns it on, so prod is safe by construction. Read at
 * call time so tests can toggle it per-test (mirrors the langfuse killswitch in
 * agents/observability/langfuse_export.py).
 */
export function isTenantAdminEnabled(): boolean {
  return ENABLED_VALUES.has(
    (process.env.TENANT_ADMIN_ENABLED ?? "").trim().toLowerCase(),
  );
}

function notFound(): HttpResponseInit {
  return { status: 404, jsonBody: { error: "not_found" } };
}

function newTenantId(): string {
  return `TEN-${randomUUID()}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

/** Map a write-path error to its HTTP response (409 on dup, else 502). */
function mapWriteError(
  err: unknown,
  ctx: InvocationContext,
  op: string,
): HttpResponseInit {
  if (err instanceof DuplicateMobileError) {
    return { status: 409, jsonBody: { error: "duplicate_mobile", message: err.message } };
  }
  ctx.error(`tenant ${op} failed`, err);
  return { status: 502, jsonBody: { error: `${op}_failed` } };
}

/** `GET /api/admin/tenants` (list) and `POST /api/admin/tenants` (create). */
export async function tenantsCollectionHandler(
  req: HttpRequest,
  ctx: InvocationContext,
): Promise<HttpResponseInit> {
  if (!isTenantAdminEnabled()) {
    return notFound();
  }
  const repo = getTenantRepository();

  if (req.method === "POST") {
    let body: unknown;
    try {
      body = await req.json();
    } catch {
      return { status: 400, jsonBody: { error: "invalid_json" } };
    }
    const result = validateTenantInput(body);
    if (!result.ok || result.value === undefined) {
      return { status: 400, jsonBody: { error: "validation_failed", fields: result.errors } };
    }
    const now = nowIso();
    const tenant: Tenant = {
      id: newTenantId(),
      ...result.value,
      created_at: now,
      updated_at: now,
    };
    try {
      return { status: 201, jsonBody: await repo.create(tenant) };
    } catch (err) {
      return mapWriteError(err, ctx, "create");
    }
  }

  try {
    return { status: 200, jsonBody: await repo.list() };
  } catch (err) {
    ctx.error("tenant list failed", err);
    return { status: 502, jsonBody: { error: "list_failed" } };
  }
}

/**
 * `GET` / `PUT` / `DELETE` on `/api/admin/tenants/{id}` — get-by-id, update,
 * delete.
 */
export async function tenantItemHandler(
  req: HttpRequest,
  ctx: InvocationContext,
): Promise<HttpResponseInit> {
  if (!isTenantAdminEnabled()) {
    return notFound();
  }
  const repo = getTenantRepository();
  const id = (req.params.id ?? "").trim();
  if (id === "") {
    return notFound();
  }

  if (req.method === "DELETE") {
    try {
      return (await repo.remove(id)) ? { status: 204 } : notFound();
    } catch (err) {
      ctx.error("tenant delete failed", err);
      return { status: 502, jsonBody: { error: "delete_failed" } };
    }
  }

  if (req.method === "PUT") {
    let body: unknown;
    try {
      body = await req.json();
    } catch {
      return { status: 400, jsonBody: { error: "invalid_json" } };
    }
    const result = validateTenantInput(body);
    if (!result.ok || result.value === undefined) {
      return { status: 400, jsonBody: { error: "validation_failed", fields: result.errors } };
    }
    let existing: Tenant | null;
    try {
      existing = await repo.get(id);
    } catch (err) {
      ctx.error("tenant get (for update) failed", err);
      return { status: 502, jsonBody: { error: "update_failed" } };
    }
    if (existing === null) {
      return notFound();
    }
    // Preserve id + created_at; bump updated_at; overwrite editable fields.
    const updated: Tenant = {
      ...result.value,
      id: existing.id,
      created_at: existing.created_at,
      updated_at: nowIso(),
    };
    try {
      const saved = await repo.update(updated);
      return saved ? { status: 200, jsonBody: saved } : notFound();
    } catch (err) {
      return mapWriteError(err, ctx, "update");
    }
  }

  try {
    const tenant = await repo.get(id);
    return tenant ? { status: 200, jsonBody: tenant } : notFound();
  } catch (err) {
    ctx.error("tenant get failed", err);
    return { status: 502, jsonBody: { error: "get_failed" } };
  }
}

// NOTE: the app.http() registrations for these handlers live in ./index.ts (the
// package.json "main" entry) — NOT here. On the SWA managed-functions host,
// registrations made outside the entry file are not served at runtime. See the
// CM-61 note in index.ts. This module owns the handlers + logic; index.ts wires
// the routes.
