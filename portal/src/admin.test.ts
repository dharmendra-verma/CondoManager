import { describe, expect, it } from "vitest";

import {
  type RawForm,
  type Tenant,
  formatOptional,
  normalizeMobile,
  sortTenants,
  summarizeErrors,
  validateTenantForm,
} from "./admin";

function form(overrides: Partial<RawForm> = {}): RawForm {
  return {
    name: "Asha Rao",
    unit: "4B",
    mobile: "+91 98765-43210",
    email: "asha@example.com",
    notes: "",
    spouse: "",
    ...overrides,
  };
}

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

describe("validateTenantForm", () => {
  it("normalizes a valid form (trim + strip mobile, empty optional -> null)", () => {
    const result = validateTenantForm(form());
    expect(result.ok).toBe(true);
    expect(result.value).toEqual({
      name: "Asha Rao",
      unit: "4B",
      mobile: "+919876543210",
      email: "asha@example.com",
      notes: null,
      spouse: null,
    });
  });

  it("flags each missing required field", () => {
    const result = validateTenantForm(form({ name: "  ", unit: "", mobile: "" }));
    expect(result.ok).toBe(false);
    expect(Object.keys(result.errors).sort()).toEqual(["mobile", "name", "unit"]);
  });

  it("rejects a malformed mobile and email", () => {
    expect(validateTenantForm(form({ mobile: "12" })).errors).toHaveProperty("mobile");
    expect(validateTenantForm(form({ email: "nope" })).errors).toHaveProperty("email");
  });

  it("does not expose a value when invalid", () => {
    expect(validateTenantForm(form({ mobile: "" })).value).toBeUndefined();
  });
});

describe("normalizeMobile", () => {
  it("strips spaces and dashes", () => {
    expect(normalizeMobile("+91 98765-43210")).toBe("+919876543210");
  });
});

describe("sortTenants", () => {
  it("orders by unit (numeric-aware) then name without mutating input", () => {
    const input = [
      tenant({ id: "a", unit: "10A", name: "Zoe" }),
      tenant({ id: "b", unit: "2B", name: "Amy" }),
      tenant({ id: "c", unit: "2B", name: "Bob" }),
    ];
    const sorted = sortTenants(input);
    expect(sorted.map((t) => t.id)).toEqual(["b", "c", "a"]);
    expect(input[0].id).toBe("a"); // original untouched
  });
});

describe("formatters", () => {
  it("formatOptional falls back to an em dash", () => {
    expect(formatOptional(null)).toBe("—");
    expect(formatOptional("  ")).toBe("—");
    expect(formatOptional("note")).toBe("note");
  });

  it("summarizeErrors joins messages", () => {
    expect(summarizeErrors({ a: "x", b: "y" })).toBe("x · y");
    expect(summarizeErrors({})).toBe("");
  });
});
