// Pure tenant model + validation for the admin CRUD API (CM-56). No Azure /
// Cosmos imports here so the logic is unit-testable under vitest/node —
// mirrors portal/api/src/shape.ts.
//
// SECURITY: tenant records contain PII (name, mobile, email). Unlike the
// public ticket projection (`toPublicTicket`), the admin API intentionally
// returns the FULL record — the protection is the separate `/api/admin` route
// plus the dev/test env gate (see tenants.ts), NOT field stripping.
// TODO(auth): real auth must land before this route is ever deployed.

/** A managed tenant master record (the `tenants` Cosmos container, PK `/id`). */
export interface Tenant {
  id: string;
  name: string;
  unit: string;
  mobile: string;
  email: string | null;
  notes: string | null;
  spouse: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * The editable fields accepted on create / update — everything except the
 * server-managed `id` / `created_at` / `updated_at`.
 */
export interface TenantInput {
  name: string;
  unit: string;
  mobile: string;
  email: string | null;
  notes: string | null;
  spouse: string | null;
}

// E.164-ish: optional leading `+`, then 7–15 digits, no leading zero. Spaces
// and dashes are stripped first so "+91 98765-43210" -> "+919876543210".
const MOBILE_RE = /^\+?[1-9]\d{6,14}$/;
// Pragmatic email check (not full RFC 5322): non-space local @ domain . tld.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface ValidationResult {
  ok: boolean;
  /** Present (and complete) only when `ok` is true. */
  value?: TenantInput;
  /** field name -> human-readable message; empty when `ok` is true. */
  errors: Record<string, string>;
}

/** Strip spaces and dashes from a mobile so formatting doesn't affect identity. */
export function normalizeMobile(raw: string): string {
  return raw.replace(/[\s-]/g, "");
}

/** Coerce an optional field: non-empty trimmed string, else `null`. */
function optionalString(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/**
 * Validate + normalize a raw request body into a `TenantInput`. Never throws:
 * returns `{ ok: false, errors }` with one message per offending field, or
 * `{ ok: true, value }` with trimmed/normalized fields.
 */
export function validateTenantInput(raw: unknown): ValidationResult {
  const body = (raw ?? {}) as Record<string, unknown>;
  const errors: Record<string, string> = {};

  const name = typeof body.name === "string" ? body.name.trim() : "";
  const unit = typeof body.unit === "string" ? body.unit.trim() : "";
  const mobile = normalizeMobile(
    typeof body.mobile === "string" ? body.mobile.trim() : "",
  );
  const email = optionalString(body.email);
  const notes = optionalString(body.notes);
  const spouse = optionalString(body.spouse);

  if (name === "") {
    errors.name = "name is required";
  }
  if (unit === "") {
    errors.unit = "unit is required";
  }
  if (mobile === "") {
    errors.mobile = "mobile is required";
  } else if (!MOBILE_RE.test(mobile)) {
    errors.mobile = "mobile must be a valid phone number (7–15 digits, optional +)";
  }
  if (email !== null && !EMAIL_RE.test(email)) {
    errors.email = "email must be a valid email address";
  }

  if (Object.keys(errors).length > 0) {
    return { ok: false, errors };
  }
  return { ok: true, value: { name, unit, mobile, email, notes, spouse }, errors: {} };
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * Project a raw Cosmos `tenants` document into a `Tenant`. Returns `null` when
 * a required field (`id`/`name`/`unit`/`mobile`) is missing — never throws.
 */
export function toTenant(doc: Record<string, unknown>): Tenant | null {
  const id = asString(doc.id);
  const name = asString(doc.name);
  const unit = asString(doc.unit);
  const mobile = asString(doc.mobile);
  if (id === null || name === null || unit === null || mobile === null) {
    return null;
  }
  return {
    id,
    name,
    unit,
    mobile,
    email: asString(doc.email),
    notes: asString(doc.notes),
    spouse: asString(doc.spouse),
    created_at: asString(doc.created_at) ?? "",
    updated_at: asString(doc.updated_at) ?? "",
  };
}
