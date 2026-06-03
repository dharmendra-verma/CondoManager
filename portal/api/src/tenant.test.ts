import { describe, expect, it } from "vitest";

import {
  type Tenant,
  normalizeMobile,
  toTenant,
  validateTenantInput,
} from "./tenant";

describe("validateTenantInput", () => {
  it("accepts a full valid record and trims/normalizes it", () => {
    const result = validateTenantInput({
      name: "  Asha Rao ",
      unit: " 4B ",
      mobile: "+91 98765-43210",
      email: " asha@example.com ",
      notes: " has a dog ",
      spouse: " Vikram ",
    });
    expect(result.ok).toBe(true);
    expect(result.value).toEqual({
      name: "Asha Rao",
      unit: "4B",
      mobile: "+919876543210",
      email: "asha@example.com",
      notes: "has a dog",
      spouse: "Vikram",
    });
  });

  it("defaults optional fields to null", () => {
    const result = validateTenantInput({ name: "A", unit: "1", mobile: "9876543210" });
    expect(result.ok).toBe(true);
    expect(result.value).toMatchObject({ email: null, notes: null, spouse: null });
  });

  it("treats empty/whitespace optional fields as null", () => {
    const result = validateTenantInput({
      name: "A",
      unit: "1",
      mobile: "9876543210",
      email: "   ",
    });
    expect(result.value?.email).toBeNull();
  });

  it("reports each missing required field", () => {
    const result = validateTenantInput({});
    expect(result.ok).toBe(false);
    expect(result.errors).toHaveProperty("name");
    expect(result.errors).toHaveProperty("unit");
    expect(result.errors).toHaveProperty("mobile");
  });

  it("rejects a malformed mobile", () => {
    const result = validateTenantInput({ name: "A", unit: "1", mobile: "12" });
    expect(result.ok).toBe(false);
    expect(result.errors).toHaveProperty("mobile");
  });

  it("rejects a malformed email", () => {
    const result = validateTenantInput({
      name: "A",
      unit: "1",
      mobile: "9876543210",
      email: "not-an-email",
    });
    expect(result.ok).toBe(false);
    expect(result.errors).toHaveProperty("email");
  });

  it("does not invent a value when validation fails", () => {
    expect(validateTenantInput({ name: "A" }).value).toBeUndefined();
  });
});

describe("normalizeMobile", () => {
  it("strips spaces and dashes only", () => {
    expect(normalizeMobile("+91 98765-43210")).toBe("+919876543210");
  });
});

describe("toTenant", () => {
  const RAW: Record<string, unknown> = {
    id: "TEN-1",
    name: "Asha Rao",
    unit: "4B",
    mobile: "+919876543210",
    email: "asha@example.com",
    notes: null,
    spouse: null,
    created_at: "2026-06-01T00:00:00.000Z",
    updated_at: "2026-06-02T00:00:00.000Z",
  };

  it("maps a well-formed doc", () => {
    expect(toTenant(RAW)).toEqual<Tenant>({
      id: "TEN-1",
      name: "Asha Rao",
      unit: "4B",
      mobile: "+919876543210",
      email: "asha@example.com",
      notes: null,
      spouse: null,
      created_at: "2026-06-01T00:00:00.000Z",
      updated_at: "2026-06-02T00:00:00.000Z",
    });
  });

  it("returns null when a required field is missing", () => {
    const { mobile: _mobile, ...noMobile } = RAW;
    expect(toTenant(noMobile)).toBeNull();
  });
});
