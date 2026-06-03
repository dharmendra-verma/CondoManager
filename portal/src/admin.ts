// Pure, framework-free helpers for the tenant admin page (CM-56). No DOM access
// here so the logic is unit-testable under vitest/node — mirrors ticket.ts.
//
// The `Tenant` / validation types are intentionally duplicated from the API's
// tenant.ts: the SPA and the Functions API are separate npm projects, exactly
// as `PublicTicket` is mirrored between ticket.ts and the API's shape.ts.

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

/** Editable fields sent to the API on create / update. */
export interface TenantInput {
  name: string;
  unit: string;
  mobile: string;
  email: string | null;
  notes: string | null;
  spouse: string | null;
}

/** Raw string values straight off the form inputs. */
export interface RawForm {
  name: string;
  unit: string;
  mobile: string;
  email: string;
  notes: string;
  spouse: string;
}

// Kept in lock-step with the API's tenant.ts so client + server agree.
const MOBILE_RE = /^\+?[1-9]\d{6,14}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface FormValidation {
  ok: boolean;
  /** Present only when `ok` is true. */
  value?: TenantInput;
  /** field -> message; empty when `ok` is true. */
  errors: Record<string, string>;
}

/** Strip spaces and dashes so formatting doesn't change mobile identity. */
export function normalizeMobile(raw: string): string {
  return raw.replace(/[\s-]/g, "");
}

function optional(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/**
 * Validate + normalize the add/edit form. Never throws: returns field errors
 * or a clean `TenantInput`. Mirrors the server's `validateTenantInput` so the
 * client can reject bad input before hitting the API.
 */
export function validateTenantForm(form: RawForm): FormValidation {
  const errors: Record<string, string> = {};
  const name = form.name.trim();
  const unit = form.unit.trim();
  const mobile = normalizeMobile(form.mobile.trim());
  const email = optional(form.email);
  const notes = optional(form.notes);
  const spouse = optional(form.spouse);

  if (name === "") {
    errors.name = "Name is required";
  }
  if (unit === "") {
    errors.unit = "Unit is required";
  }
  if (mobile === "") {
    errors.mobile = "Mobile is required";
  } else if (!MOBILE_RE.test(mobile)) {
    errors.mobile = "Enter a valid phone number (7–15 digits, optional +)";
  }
  if (email !== null && !EMAIL_RE.test(email)) {
    errors.email = "Enter a valid email address";
  }

  if (Object.keys(errors).length > 0) {
    return { ok: false, errors };
  }
  return { ok: true, value: { name, unit, mobile, email, notes, spouse }, errors: {} };
}

/** Display order: by unit (numeric-aware), then name. Does not mutate input. */
export function sortTenants(tenants: Tenant[]): Tenant[] {
  return [...tenants].sort(
    (a, b) =>
      a.unit.localeCompare(b.unit, undefined, { numeric: true }) ||
      a.name.localeCompare(b.name),
  );
}

/** Render an optional cell value, falling back to an em dash. */
export function formatOptional(value: string | null): string {
  return value && value.trim() ? value : "—";
}

/** Flatten a field-errors map into a single human-readable line. */
export function summarizeErrors(errors: Record<string, string>): string {
  return Object.values(errors).join(" · ");
}
