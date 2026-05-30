import { describe, expect, it } from "vitest";

import {
  TICKET_STATES,
  formatEta,
  formatVendor,
  statusTimeline,
  validateCode,
} from "./ticket";

describe("validateCode", () => {
  it("accepts a well-formed code and upper-cases it", () => {
    expect(validateCode("tkt-3f8a2c9d")).toBe("TKT-3F8A2C9D");
    expect(validateCode("  TKT-ABCDEF01  ")).toBe("TKT-ABCDEF01");
  });

  it("rejects malformed codes", () => {
    expect(validateCode("")).toBeNull();
    expect(validateCode("3F8A2C9D")).toBeNull();
    expect(validateCode("TKT-XYZ")).toBeNull(); // non-hex / wrong length
    expect(validateCode("TKT-3F8A2C9DD")).toBeNull(); // too long
    expect(validateCode("'; DROP TABLE")).toBeNull();
  });
});

describe("statusTimeline", () => {
  it("marks one current step with prior steps done", () => {
    const steps = statusTimeline("Waiting");
    expect(steps.map((s) => s.state)).toEqual([
      "done",
      "done",
      "current",
      "upcoming",
    ]);
    expect(steps.map((s) => s.label)).toEqual([...TICKET_STATES]);
  });

  it("marks every step done at the resolved terminal", () => {
    expect(statusTimeline("Resolved").map((s) => s.state)).toEqual([
      "done",
      "done",
      "done",
      "current",
    ]);
  });

  it("marks only the first step current when new", () => {
    expect(statusTimeline("New")[0].state).toBe("current");
    expect(statusTimeline("New").slice(1).every((s) => s.state === "upcoming")).toBe(
      true,
    );
  });
});

describe("formatters", () => {
  it("falls back when vendor/eta are missing", () => {
    expect(formatVendor(null)).toBe("Not yet assigned");
    expect(formatVendor("  ")).toBe("Not yet assigned");
    expect(formatVendor("Ace Plumbing")).toBe("Ace Plumbing");
    expect(formatEta(null)).toBe("To be confirmed");
    expect(formatEta("within 2 hours")).toBe("within 2 hours");
  });
});
