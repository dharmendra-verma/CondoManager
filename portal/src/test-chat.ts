// Pure, framework-free logic for the TEST-ONLY web chat channel (CM-55).
// No DOM and no storage access here, so it is unit-testable under vitest/node;
// the DOM glue lives in ./test-chat-view. Session state is held in a plain
// in-memory object (see ChatSession) — never localStorage — so a page refresh
// requires re-login (AC: session state is in-memory only).
//
// `normalizeMobile` / the mobile regex are intentionally duplicated from
// ./admin (and kept in lock-step with the backend's tenants.normalize_mobile),
// exactly as the codebase already mirrors shapes between the SPA and the API.

export interface TenantSession {
  tenant_id: string;
  name: string;
  unit: string;
}

export interface ChatTurn {
  role: "tenant" | "manager";
  text: string;
  /** True when the manager turn is a labelled stub (no live agent reply). */
  stub?: boolean;
}

/** Response shape from `POST /web/message`. */
export interface MessageResponse {
  reply: string;
  stub: boolean;
  channel: string;
  intent: string | null;
}

const MOBILE_RE = /^\+?[1-9]\d{6,14}$/;

/** Strip spaces and dashes so formatting doesn't change mobile identity. */
export function normalizeMobile(raw: string): string {
  return raw.replace(/[\s-]/g, "");
}

export type MobileValidation =
  | { ok: true; mobile: string }
  | { ok: false; error: string };

/** Validate + normalize a mobile number. Never throws. */
export function validateMobile(raw: string): MobileValidation {
  const mobile = normalizeMobile(raw.trim());
  if (mobile === "") {
    return { ok: false, error: "Enter your mobile number." };
  }
  if (!MOBILE_RE.test(mobile)) {
    return {
      ok: false,
      error: "That doesn't look like a valid mobile number (e.g. +919876543210).",
    };
  }
  return { ok: true, mobile };
}

/** Body for `POST /web/login`. */
export function loginPayload(mobile: string): { mobile: string } {
  return { mobile: normalizeMobile(mobile.trim()) };
}

/** Body for `POST /web/message`. */
export function messagePayload(
  mobile: string,
  content: string,
): { mobile: string; content: string } {
  return { mobile: normalizeMobile(mobile.trim()), content: content.trim() };
}

/** Compose the manager chat turn from a backend response. */
export function managerTurn(resp: MessageResponse): ChatTurn {
  return { role: "manager", text: resp.reply, stub: resp.stub };
}

/**
 * In-memory chat session. Deliberately NOT persisted anywhere (no localStorage,
 * no tokens) — a page refresh drops it and forces a re-login, per the AC.
 */
export class ChatSession {
  readonly tenant: TenantSession;
  readonly turns: ChatTurn[] = [];

  constructor(tenant: TenantSession) {
    this.tenant = tenant;
  }

  addTenant(text: string): ChatTurn {
    const turn: ChatTurn = { role: "tenant", text };
    this.turns.push(turn);
    return turn;
  }

  addManager(resp: MessageResponse): ChatTurn {
    const turn = managerTurn(resp);
    this.turns.push(turn);
    return turn;
  }
}
