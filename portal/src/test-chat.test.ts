import { describe, expect, it } from "vitest";

import {
  ChatSession,
  loginPayload,
  managerTurn,
  messagePayload,
  normalizeMobile,
  validateMobile,
} from "./test-chat";

describe("validateMobile", () => {
  it("accepts a valid E.164 number, stripping spaces and dashes", () => {
    expect(validateMobile("+91 98765-43210")).toEqual({
      ok: true,
      mobile: "+919876543210",
    });
  });

  it("rejects an empty number", () => {
    const r = validateMobile("   ");
    expect(r.ok).toBe(false);
  });

  it("rejects a malformed number", () => {
    const r = validateMobile("12");
    expect(r.ok).toBe(false);
  });
});

describe("normalizeMobile", () => {
  it("strips spaces and dashes", () => {
    expect(normalizeMobile("+91 98765-43210")).toBe("+919876543210");
  });
});

describe("request payloads", () => {
  it("loginPayload normalizes the mobile number", () => {
    expect(loginPayload(" +91 98765-43210 ")).toEqual({ mobile: "+919876543210" });
  });

  it("messagePayload normalizes mobile and trims content", () => {
    expect(messagePayload(" +919876543210 ", "  hello  ")).toEqual({
      mobile: "+919876543210",
      content: "hello",
    });
  });
});

describe("managerTurn", () => {
  it("carries the reply text and stub flag", () => {
    expect(
      managerTurn({ reply: "thanks", stub: true, channel: "web", intent: "inquiry" }),
    ).toEqual({ role: "manager", text: "thanks", stub: true });
  });
});

describe("ChatSession (in-memory only)", () => {
  it("accumulates turns in order and exposes the tenant", () => {
    const s = new ChatSession({ tenant_id: "condo-tower-a", name: "Asha", unit: "4B" });
    s.addTenant("leak in the sink");
    s.addManager({ reply: "ack", stub: false, channel: "web", intent: "maintenance" });
    expect(s.tenant.unit).toBe("4B");
    expect(s.turns.map((t) => t.role)).toEqual(["tenant", "manager"]);
    expect(s.turns[1]).toEqual({ role: "manager", text: "ack", stub: false });
  });

  it("never persists to localStorage (session lives only in memory)", () => {
    const s = new ChatSession({ tenant_id: "t", name: "n", unit: "u" });
    s.addTenant("hi");
    // The pure module must not shim a storage backend into the node env.
    expect(typeof globalThis.localStorage).toBe("undefined");
  });
});
